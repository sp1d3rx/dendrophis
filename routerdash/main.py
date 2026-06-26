import hashlib
import json
import re
import socket
import threading
import time

import requests
from analyze import OMLX_MODEL, _extract_json, classify_batch, classify_entries, get_cache_stats
from flask import Flask, jsonify, render_template, request
from markupsafe import Markup

app = Flask(__name__)

ROUTER = "http://192.168.1.1"
USERNAME = "root"
PASSWORD = "CQepTHsg69tkY3Y"

SESSION_CACHE = {"id": None, "expires": 0}
SESSION_TTL = 300  # seconds — LuCI sessions last much longer, this is conservative

LOG_RE = re.compile(
    r"^(?P<date>\w{3}\s+\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+"
    r"(?P<year>\d{4})\s+"
    r"(?P<facility>\w+)\.(?P<level>\w+)\s+"
    r"(?P<process>\S+?)(?:\[(?P<pid>\d+)\])?:\s+"
    r"(?P<message>.*)$"
)


def get_session():
    """Authenticate with LuCI and return a session ID, using cached cookie if valid."""
    now = time.time()
    if SESSION_CACHE["id"] and now < SESSION_CACHE["expires"]:
        return SESSION_CACHE["id"]

    resp = requests.post(
        f"{ROUTER}/cgi-bin/luci/admin/",
        data={"luci_username": USERNAME, "luci_password": PASSWORD},
        allow_redirects=False,
    )
    if resp.status_code not in (200, 302):
        raise RuntimeError(f"Login failed: {resp.status_code}")
    cookies = list(resp.cookies)
    if not cookies:
        raise RuntimeError("No session cookie returned")
    for cookie in cookies:
        if cookie.name in ("sysauth", "session", "ubus"):
            session_id = cookie.value
            SESSION_CACHE["id"] = session_id
            SESSION_CACHE["expires"] = now + SESSION_TTL
            return session_id
    session_id = cookies[0].value
    SESSION_CACHE["id"] = session_id
    SESSION_CACHE["expires"] = now + SESSION_TTL
    return session_id


def fetch_logs(session_id):
    """Fetch syslog entries via cgi-exec."""
    resp = requests.post(
        f"{ROUTER}/cgi-bin/cgi-exec",
        data={
            "sessionid": session_id,
            "command": "/usr/libexec/syslog-wrapper",
        },
        timeout=10,
    )
    if resp.status_code in (401, 403):
        SESSION_CACHE["id"] = None
        new_session_id = get_session()
        resp = requests.post(
            f"{ROUTER}/cgi-bin/cgi-exec",
            data={
                "sessionid": new_session_id,
                "command": "/usr/libexec/syslog-wrapper",
            },
            timeout=10,
        )
    return resp.text.splitlines()


def parse_dnsmasq(message):
    """Parse dnsmasq message: timestamp client action details."""
    parts = message.split(None, 2)
    if len(parts) < 3:
        return {"action": "", "detail": "", "client": "", "result": ""}

    client = parts[1].split("/")[0] if "/" in parts[1] else parts[1]
    rest = parts[2]

    # query[TYPE] domain from client_ip
    m = re.match(r"query\[(\w+)\]\s+(\S+)\s+from\s+(\S+)", rest)
    if m:
        return {"action": "query", "detail": f"{m.group(1)} {m.group(2)}", "client": m.group(3), "result": ""}

    # cached domain is result
    m = re.match(r"cached\s+(\S+)\s+is\s+(.+)", rest)
    if m:
        return {"action": "cached", "detail": m.group(1), "client": client, "result": m.group(2).strip()}

    # forwarded domain to upstream
    m = re.match(r"forwarded\s+(\S+)\s+to\s+(\S+)", rest)
    if m:
        return {"action": "forwarded", "detail": m.group(1), "client": client, "result": m.group(2)}

    # reply domain is result
    m = re.match(r"reply\s+(\S+)\s+is\s+(.+)", rest)
    if m:
        return {"action": "reply", "detail": m.group(1), "client": client, "result": m.group(2).strip()}

    # DHCP messages
    m = re.match(r"DHCP\s+(\S+)\s+is\s+(\S+)", rest)
    if m:
        return {"action": "dhcp", "detail": m.group(1), "client": client, "result": m.group(2)}

    return {"action": "", "detail": "", "client": client, "result": ""}


def parse_netifd(message):
    """Parse netifd message: Interface 'name' is state."""
    m = re.match(r"Interface\s+'([^']+)'(?:\s+is\s+(.+))?", message)
    if m:
        return {"action": (m.group(2) or "").strip(), "detail": m.group(1), "client": "", "result": ""}
    return {"action": "", "detail": "", "client": "", "result": ""}


def parse_uhttpd(message):
    """Parse uhttpd/luci message."""
    m = re.match(r"\[.*?\]\s+luci:\s+(.+)", message)
    if m:
        rest = m.group(1)
        m2 = re.match(r"accepted login on (\S+) for (\S+) from (\S+)", rest)
        if m2:
            return {"action": "login", "detail": m2.group(1), "client": m2.group(3), "result": m2.group(2)}
    return {"action": "", "detail": "", "client": "", "result": ""}


PROCESS_PARSERS = {
    "dnsmasq": parse_dnsmasq,
    "netifd": parse_netifd,
    "uhttpd": parse_uhttpd,
}


def entry_hash(entry):
    """Return an md5 hash of date + message for stable entry identification."""
    return hashlib.md5(f"{entry['date']}|{entry['message']}".encode()).hexdigest()


def parse_and_merge(raw_lines):
    """Parse syslog lines, merge continuations, reverse to newest-first."""
    entries = []
    for line in raw_lines:
        if not line.strip():
            continue
        m = LOG_RE.match(line)
        if m:
            entry = {
                "date": f"{m.group('date')} {m.group('year')}",
                "facility": m.group("facility"),
                "level": m.group("level"),
                "process": m.group("process"),
                "pid": m.group("pid") or "",
                "message": m.group("message"),
            }
            # Parse process-specific sub-fields
            parser = PROCESS_PARSERS.get(entry["process"])
            if parser:
                entry.update(parser(entry["message"]))
            else:
                entry.update({"action": "", "detail": "", "client": "", "result": ""})
            entries.append(entry)
        elif entries:
            # Continuation line — append to previous entry
            entries[-1]["message"] += "\n" + line.strip()
        else:
            # Malformed line with no parent
            entries.append(
                {
                    "date": "",
                    "facility": "",
                    "level": "",
                    "process": "",
                    "pid": "",
                    "message": line.strip(),
                    "action": "",
                    "detail": "",
                    "client": "",
                    "result": "",
                }
            )

    # Reverse so newest entries appear first
    entries.reverse()

    # Add hash to each entry
    for entry in entries:
        entry["hash"] = entry_hash(entry)

    return entries


def ubus_call(session_id, obj, method, params=None):
    """Make a ubus JSON-RPC call and return the result."""
    if params is None:
        params = {}
    payload = {"jsonrpc": "2.0", "id": 1, "method": "call", "params": [session_id, obj, method, params]}
    cookies = get_session_cookies()
    resp = requests.post(
        f"{ROUTER}/ubus/",
        json=payload,
        cookies=cookies,
        timeout=15,
    )
    if resp.status_code in (401, 403):
        SESSION_CACHE["id"] = None
        new_session_id = get_session()
        new_cookies = {"sysauth_http": new_session_id}
        payload["params"][0] = new_session_id
        resp = requests.post(
            f"{ROUTER}/ubus/",
            json=payload,
            cookies=new_cookies,
            timeout=15,
        )
    result = resp.json()
    if "error" in result:
        raise RuntimeError(f"ubus error: {result['error']}")
    return result["result"]


def get_session_cookies():
    """Return cookies dict for ubus/authenticated requests."""
    sid = get_session()
    return {"sysauth_http": sid}


DNS_CACHE = {}
DNS_CACHE_LOCK = threading.Lock()
DNS_NEGATIVE_TTL = 600  # 10 minutes
DNS_POSITIVE_TTL = 3600  # 1 hour


def _resolve_worker(ip_address, result_list):
    try:
        hostname, _, _ = socket.gethostbyaddr(ip_address)
        result_list.append(hostname)
    except Exception:
        pass


def resolve_ip(ip_address: str) -> str:
    """Resolve an IP address to a hostname with positive and negative caching."""
    if not ip_address:
        return ""
    now = time.time()

    with DNS_CACHE_LOCK:
        cached = DNS_CACHE.get(ip_address)
        if cached and now < cached["expires"]:
            return cached["name"]

    result_list = []
    thread = threading.Thread(target=_resolve_worker, args=(ip_address, result_list))
    thread.daemon = True
    thread.start()
    thread.join(timeout=0.3)  # Wait at most 300ms

    if result_list:
        resolved_name = result_list[0]
        ttl = DNS_POSITIVE_TTL
    else:
        resolved_name = ip_address
        ttl = DNS_NEGATIVE_TTL

    with DNS_CACHE_LOCK:
        DNS_CACHE[ip_address] = {"name": resolved_name, "expires": now + ttl}
    return resolved_name


def resolve_ips_batch(ip_addresses: list[str]) -> None:
    """Resolve a list of IP addresses in parallel to populate the DNS cache."""
    unique_ips = list({ip_address for ip_address in ip_addresses if ip_address})
    now = time.time()

    # Filter to only uncached IPs
    uncached_ips = []
    with DNS_CACHE_LOCK:
        for ip_address in unique_ips:
            cached = DNS_CACHE.get(ip_address)
            if not cached or now >= cached["expires"]:
                uncached_ips.append(ip_address)

    if not uncached_ips:
        return

    # Resolve uncached IPs in parallel threads
    threads = []
    results = {}

    for ip_address in uncached_ips:
        results[ip_address] = []
        thread = threading.Thread(target=_resolve_worker, args=(ip_address, results[ip_address]))
        thread.daemon = True
        threads.append(thread)
        thread.start()

    # Wait for all threads with a single collective timeout
    for thread in threads:
        thread.join(timeout=0.3)

    # Update cache with results
    with DNS_CACHE_LOCK:
        for ip_address in uncached_ips:
            result_list = results[ip_address]
            if result_list:
                DNS_CACHE[ip_address] = {"name": result_list[0], "expires": now + DNS_POSITIVE_TTL}
            else:
                DNS_CACHE[ip_address] = {"name": ip_address, "expires": now + DNS_NEGATIVE_TTL}


@app.route("/")
def home():
    """Serve the dashboard with logs pre-loaded."""
    try:
        session_id = get_session()
        raw_lines = fetch_logs(session_id)
        logs = parse_and_merge(raw_lines)
        logs_json = Markup(json.dumps(logs))
        return render_template("index.html", initial_logs=logs_json, error=None)
    except Exception as error:
        return render_template("index.html", initial_logs="null", error=str(error))


def ubus_call_raw(cookies, obj, method, params=None):
    """Make a ubus JSON-RPC call and return the inner result dict."""
    sid = get_session()
    if params is None:
        params = {}
    payload = {"jsonrpc": "2.0", "id": 1, "method": "call", "params": [sid, obj, method, params]}
    resp = requests.post(
        f"{ROUTER}/ubus/",
        json=payload,
        cookies=cookies,
        timeout=15,
    )
    if resp.status_code in (401, 403):
        SESSION_CACHE["id"] = None
        new_session_id = get_session()
        new_cookies = {"sysauth_http": new_session_id}
        payload["params"][0] = new_session_id
        resp = requests.post(
            f"{ROUTER}/ubus/",
            json=payload,
            cookies=new_cookies,
            timeout=15,
        )
    result = resp.json()
    if "error" in result:
        raise RuntimeError(f"ubus error: {result['error']}")
    # Result format: [0, {"result": [...]}]
    res = result.get("result", [])
    return res[1] if len(res) > 1 else res[0] if res else {}


# --- Bandwidth monitoring ---
BANDWIDTH_DEVICES = [
    {"name": "wan", "label": "WAN"},
    {"name": "br-lan", "label": "LAN"},
    {"name": "wl0-ap0", "label": "WiFi"},
]


def compute_throughput(samples):
    """Compute throughput from the last two cumulative samples.

    Each sample: [timestamp, rx_bytes, rx_packets, tx_bytes, tx_packets]
    Returns dict with rx_bytes/s, tx_bytes/s, rx_packets/s, tx_packets/s,
    or zeros if insufficient data.
    """
    if len(samples) < 2:
        return {"rx_bytes": 0, "tx_bytes": 0, "rx_packets": 0, "tx_packets": 0}

    prev = samples[-2]
    curr = samples[-1]
    dt = curr[0] - prev[0]
    if dt <= 0:
        return {"rx_bytes": 0, "tx_bytes": 0, "rx_packets": 0, "tx_packets": 0}

    return {
        "rx_bytes": (curr[1] - prev[1]) / dt,
        "tx_bytes": (curr[3] - prev[3]) / dt,
        "rx_packets": (curr[2] - prev[2]) / dt,
        "tx_packets": (curr[4] - prev[4]) / dt,
    }


@app.route("/api/bandwidth")
def api_bandwidth():
    """Return current throughput for each monitored device."""
    try:
        cookies = get_session_cookies()
        devices = []
        for dev in BANDWIDTH_DEVICES:
            try:
                result = ubus_call_raw(
                    cookies,
                    "luci",
                    "getRealtimeStats",
                    {
                        "mode": "interface",
                        "device": dev["name"],
                    },
                )
                samples = result.get("result", [])
                tp = compute_throughput(samples)
                devices.append(
                    {
                        "name": dev["name"],
                        "label": dev["label"],
                        **tp,
                    }
                )
            except Exception as e:
                devices.append(
                    {
                        "name": dev["name"],
                        "label": dev["label"],
                        "rx_bytes": 0,
                        "tx_bytes": 0,
                        "rx_packets": 0,
                        "tx_packets": 0,
                        "error": str(e),
                    }
                )

        return jsonify({"devices": devices})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/connections")
def api_connections():
    """Return active connections via luci.getConntrackList."""
    try:
        cookies = get_session_cookies()
        result = ubus_call_raw(cookies, "luci", "getConntrackList")
        conntrack_list = result.get("result", [])

        # Batch resolve IPs in parallel to populate DNS cache
        ips_to_resolve = []
        for connection_item in conntrack_list:
            if connection_item.get("src"):
                ips_to_resolve.append(connection_item["src"])
            if connection_item.get("dst"):
                ips_to_resolve.append(connection_item["dst"])
        resolve_ips_batch(ips_to_resolve)

        connections = [
            {
                "src": ct.get("src", ""),
                "src_name": resolve_ip(ct.get("src", "")),
                "dst": ct.get("dst", ""),
                "dst_name": resolve_ip(ct.get("dst", "")),
                "sport": ct.get("sport", ""),
                "dport": ct.get("dport", ""),
                "layer3": ct.get("layer3", ""),
                "layer4": ct.get("layer4", ""),
                "bytes": ct.get("bytes", 0),
                "packets": ct.get("packets", 0),
                "timeout": ct.get("timeout", 0),
                "zone": ct.get("zone", ""),
            }
            for ct in conntrack_list
        ]

        # Search filter
        query_str = request.args.get("q", "").strip().lower()
        if query_str:
            connections = [
                connection
                for connection in connections
                if query_str in connection["src"].lower()
                or query_str in connection["dst"].lower()
                or query_str in connection["src_name"].lower()
                or query_str in connection["dst_name"].lower()
                or query_str in str(connection["dport"])
            ]

        # Sort by bytes descending
        connections.sort(key=lambda connection: connection["bytes"], reverse=True)

        return jsonify(
            {
                "connections": connections,
                "total": len(connections),
            }
        )
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@app.route("/api/logs/delta", methods=["POST"])
def api_logs_delta():
    """Return only new entries since client's last known state."""
    try:
        session_id = get_session()
        raw_lines = fetch_logs(session_id)
        logs = parse_and_merge(raw_lines)

        # Client sends hashes of entries it already knows about in the JSON body
        data = request.get_json() or {}
        known_hashes = set(data.get("known_hashes", []))

        if not known_hashes:
            # First delta call — no state, return full sync signal
            return jsonify({"full_sync": True})

        # Find the first known entry (newest-first list, so scan from start)
        known_index = None
        for i, entry in enumerate(logs):
            if entry["hash"] in known_hashes:
                known_index = i
                break

        if known_index is None:
            # None of the known entries found — buffer wrapped, full sync needed
            return jsonify({"full_sync": True})

        # New entries are everything before the known entry (newest-first)
        new_entries = logs[:known_index]

        # Find evicted entries — hashes the client knows that are no longer in the buffer
        current_hashes = {e["hash"] for e in logs}
        evicted_hashes = [h for h in known_hashes if h not in current_hashes]

        return jsonify(
            {
                "new": new_entries,
                "evicted": evicted_hashes,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/logs")
def api_logs():
    """Return logs as JSON with filtering and pagination."""
    try:
        session_id = get_session()
        raw_lines = fetch_logs(session_id)
        logs = parse_and_merge(raw_lines)

        # Apply filters (AND logic)
        q = request.args.get("q", "").strip().lower()
        facility = request.args.get("facility", "")
        process = request.args.get("process", "")
        level = request.args.get("level", "")
        action = request.args.get("action", "")
        client = request.args.get("client", "")
        detail = request.args.get("detail", "")
        result = request.args.get("result", "")

        if q:
            logs = [e for e in logs if q in e["message"].lower()]
        if facility:
            logs = [e for e in logs if e["facility"] == facility]
        if process:
            logs = [e for e in logs if e["process"] == process]
        if level:
            logs = [e for e in logs if e["level"] == level]
        if action:
            logs = [e for e in logs if e["action"] == action]
        if client:
            logs = [e for e in logs if e["client"] == client]
        if detail:
            logs = [e for e in logs if e["detail"] == detail]
        if result:
            logs = [e for e in logs if e["result"] == result]

        # Collect distinct values from filtered dataset
        facilities = sorted({e["facility"] for e in logs if e["facility"]})
        processes = sorted({e["process"] for e in logs if e["process"]})
        levels = sorted({e["level"] for e in logs if e["level"]})
        actions = sorted({e["action"] for e in logs if e["action"]})
        clients = sorted({e["client"] for e in logs if e["client"]})
        details = sorted({e["detail"] for e in logs if e["detail"]})
        results = sorted({e["result"] for e in logs if e["result"]})

        # Pagination
        per_page = request.args.get("per_page", 25, type=int)
        page = request.args.get("page", 1, type=int)
        total = len(logs)

        if per_page == 0:
            # Return all logs (used for full sync)
            return jsonify({"logs": logs})

        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))
        start = (page - 1) * per_page
        page_logs = logs[start : start + per_page]

        return jsonify(
            {
                "logs": page_logs,
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": total_pages,
                "facilities": facilities,
                "processes": processes,
                "levels": levels,
                "actions": actions,
                "clients": clients,
                "details": details,
                "results": results,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --- Log Classification via LLM ---


@app.route("/api/logs/classified")
def api_logs_classified():
    """Return logs with LLM classification (category, severity, summary, tags)."""
    try:
        session_id = get_session()
        raw_lines = fetch_logs(session_id)
        logs = parse_and_merge(raw_lines)

        # Classify most recent 50 entries
        logs = classify_entries(logs[:50])

        return jsonify(
            {
                "logs": logs,
                "count": len(logs),
            }
        )
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    """Send logs to the LLM for batch analysis / anomaly detection.

    Body (JSON):
        - action: "classify" | "anomaly" | "summary"
        - logs: list of log entry dicts (or empty for anomaly/summary which fetches fresh)
        - batch_size: max messages to send in one batch (default 50)
    """
    try:
        data = request.get_json() or {}
        action = data.get("action", "classify")
        provided_logs = data.get("logs")
        batch_size = data.get("batch_size", 50)

        if action == "classify":
            # Classify provided logs, or fetch fresh if none given
            if not provided_logs:
                session_id = get_session()
                raw_lines = fetch_logs(session_id)
                logs = parse_and_merge(raw_lines)
                provided_logs = logs[:batch_size]
            classify_batch(provided_logs)
            return jsonify({"entries": provided_logs})

        if action == "anomaly":
            # Fetch fresh logs if none provided, send to LLM for anomaly detection
            if not provided_logs:
                session_id = get_session()
                raw_lines = fetch_logs(session_id)
                logs = parse_and_merge(raw_lines)
                provided_logs = logs[:batch_size]
            messages = [entry.get("message", "") for entry in provided_logs]

            prompt = f"""\
You are a network monitoring assistant analyzing router syslog entries. \
Here are the most recent log entries from a home router.

Analyze these entries and identify:
1. Any security concerns (failed logins, suspicious activity, etc.)
2. Any unusual patterns (interface flapping, repeated failures, etc.)
3. Any events that deserve attention
Networking context:
- DNS queries for the same domain from multiple local devices (multiple source IPs
  querying the same common domain like apple.com, google.com, smartthings.com, etc.)
  are 100% NORMAL behavior. Devices on a local network accessing shared services,
  OS updates, or smart home backends will query the same domains. This is NOT a botnet,
  compromise, or malicious activity.
- Multiple destination IPs returned/resolved for the same domain is standard CDN,
  round-robin DNS, and load-balancing behavior. Do NOT flag this as suspicious.
- A device querying the same domain repeatedly is normal (cache misses, app heartbeats,
  periodic lookups).
- Do NOT flag repeated DNS queries as "static configuration", "misrouting", or
  "compromised devices" -- that is expected network behavior.
- A "concern" should only be raised for genuinely unusual events: failed auth attempts,
  interface flapping (up/down/up), unexpected ports, or high-frequency queries to unknown/suspicious domains.



Log entries:
{chr(10).join(f"[{index + 1}] {log_message}" for index, log_message in enumerate(messages))}

Return a JSON object with:
- concerns: array of objects with "type" (string), "severity" ("low"/"medium"/"high"), "description" (string)
- summary: brief overview of what you noticed (2-4 sentences)
- suggestion: any recommended action (or "none" if everything looks normal)

Return ONLY the JSON object."""

            payload = {
                "model": OMLX_MODEL,
                "messages": [
                    {"role": "system", "content": "You are a network security and monitoring analyst."},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 2048,
                "temperature": 0.3,
            }

            import urllib.request

            req = urllib.request.Request(
                "http://127.0.0.1:8000/v1/chat/completions",
                data=json.dumps(payload).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer vUYmhvvVwRSwW58",
                },
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=300)
            result = json.loads(resp.read().decode())
            content = result["choices"][0]["message"]["content"]
            analysis = json.loads(_extract_json(content))
            return jsonify(analysis)

        if action == "summary":
            # Fetch fresh logs if none provided, generate a summary
            if not provided_logs:
                session_id = get_session()
                raw_lines = fetch_logs(session_id)
                logs = parse_and_merge(raw_lines)
                provided_logs = logs[:batch_size]
            messages = [entry.get("message", "") for entry in provided_logs]

            prompt = f"""\
You are a network monitoring assistant. Summarize the network activity from \
these recent router log entries in a way useful for a home network owner.

Networking context:
- DNS queries for the same domain from multiple local devices (multiple source IPs
  querying the same common domain like apple.com, google.com, smartthings.com, etc.)
  are 100% NORMAL behavior. Devices on a local network accessing shared services,
  OS updates, or smart home backends will query the same domains. This is NOT a botnet,
  compromise, or malicious activity.
- Multiple destination IPs returned/resolved for the same domain is standard CDN,
  round-robin DNS, and load-balancing behavior. Do NOT flag this as suspicious.
- A device querying the same domain repeatedly is normal (cache misses, app heartbeats,
  periodic lookups).
- Do NOT flag repeated DNS queries as "static configuration", "misrouting", or
  "compromised devices" -- that is expected network behavior.
- A "concern" should only be raised for genuinely unusual events: failed auth attempts,
  interface flapping (up/down/up), unexpected ports, or high-frequency queries to unknown/suspicious domains.

Log entries:
{chr(10).join(f"[{index + 1}] {log_message}" for index, log_message in enumerate(messages))}

Return a JSON object with:
- overview: a short paragraph summarizing what happened (3-5 sentences)
- key_events: array of the 3-5 most notable events
- devices_mentioned: array of device names/IPs mentioned (if any)
- recommendations: any recommended actions (or "none" if everything is normal)

Return ONLY the JSON object."""

            payload = {
                "model": OMLX_MODEL,
                "messages": [
                    {"role": "system", "content": "You are a helpful network monitoring assistant."},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 2048,
                "temperature": 0.3,
            }

            import urllib.request

            req = urllib.request.Request(
                "http://127.0.0.1:8000/v1/chat/completions",
                data=json.dumps(payload).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer vUYmhvvVwRSwW58",
                },
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=300)
            result = json.loads(resp.read().decode())
            content = result["choices"][0]["message"]["content"]

            analysis = json.loads(_extract_json(content))
            return jsonify(analysis)

        return jsonify({"error": f"Unknown action: {action}"}), 400

    except Exception as error:
        return jsonify({"error": str(error)}), 500


def is_private_ip(ip_address: str) -> bool:
    """Return True if the IP address is in a private/local range."""
    ip_address = ip_address.strip().lower()
    if not ip_address:
        return True

    # Quick checks for loopback and local schemes
    if ip_address in ("localhost", "::1") or ip_address.startswith("127."):
        return True

    # IPv4 Private ranges:
    # 10.x.x.x
    # 192.168.x.x
    # 172.16.x.x to 172.31.x.x
    ipv4_pattern = re.match(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$", ip_address)
    if ipv4_pattern:
        octet_one = int(ipv4_pattern.group(1))
        octet_two = int(ipv4_pattern.group(2))
        if octet_one == 10:
            return True
        if octet_one == 192 and octet_two == 168:
            return True
        return octet_one == 172 and 16 <= octet_two <= 31

    # IPv6 Local/Link-local ranges:
    # fe80:... (link-local)
    # fc00:... or fd00:... (unique local address)
    if ip_address.startswith(("fe8", "fe9", "fea", "feb")):
        return True
    return ip_address.startswith(("fc", "fd"))


@app.route("/api/connections/analyze")
def api_connections_analyze():
    """Fetch active WAN connections, aggregate them, and analyze using LLM."""
    try:
        cookies = get_session_cookies()
        result = ubus_call_raw(cookies, "luci", "getConntrackList")
        conntrack_list = result.get("result", [])

        # Batch resolve IPs in parallel to populate DNS cache
        ips_to_resolve = []
        for connection in conntrack_list:
            if connection.get("src"):
                ips_to_resolve.append(connection["src"])
            if connection.get("dst"):
                ips_to_resolve.append(connection["dst"])
        resolve_ips_batch(ips_to_resolve)

        # Aggregate conntrack entries by (src, dst, dport, layer4) and filter out LAN-to-LAN
        aggregated_connections = {}
        for connection in conntrack_list:
            source_ip = connection.get("src", "")
            destination_ip = connection.get("dst", "")
            destination_port = connection.get("dport", "")
            protocol = connection.get("layer4", "")
            bytes_count = int(connection.get("bytes", 0))
            packets_count = int(connection.get("packets", 0))

            # Filter out LAN-to-LAN traffic (both source and destination are private)
            if is_private_ip(source_ip) and is_private_ip(destination_ip):
                continue

            connection_key = (source_ip, destination_ip, destination_port, protocol)
            if connection_key not in aggregated_connections:
                aggregated_connections[connection_key] = {"bytes": 0, "packets": 0}

            aggregated_connections[connection_key]["bytes"] += bytes_count
            aggregated_connections[connection_key]["packets"] += packets_count

        # Convert to list and sort by bytes descending
        connection_list = []
        for connection_key, stats in aggregated_connections.items():
            connection_list.append(
                {
                    "src": connection_key[0],
                    "dst": connection_key[1],
                    "dport": connection_key[2],
                    "proto": connection_key[3],
                    "bytes": stats["bytes"],
                    "packets": stats["packets"],
                }
            )

        connection_list.sort(key=lambda item: item["bytes"], reverse=True)

        # Take top 30 active external connections
        top_connections = connection_list[:30]
        if not top_connections:
            return jsonify(
                {
                    "summary": "No active WAN (external) traffic detected on the home router.",
                    "top_consumers": [],
                    "security_concerns": [],
                    "recommendations": ["None"],
                }
            )

        formatted_connections = []
        for connection in top_connections:
            bytes_val = connection["bytes"]
            if bytes_val >= 1048576:
                bytes_str = f"{bytes_val / 1048576:.1f}MB"
            elif bytes_val >= 1024:
                bytes_str = f"{bytes_val / 1024:.1f}KB"
            else:
                bytes_str = f"{bytes_val}B"

            source_ip = connection["src"]
            destination_ip = connection["dst"]
            source_name = resolve_ip(source_ip)
            destination_name = resolve_ip(destination_ip)

            source_display = f"{source_name} ({source_ip})" if source_name and source_name != source_ip else source_ip

            destination_display = (
                f"{destination_name} ({destination_ip})"
                if destination_name and destination_name != destination_ip
                else destination_ip
            )

            formatted_connections.append(
                f"[{connection['proto'].upper()}] {source_display} -> "
                f"{destination_display}:{connection['dport']} "
                f"(Bytes: {bytes_str}, Packets: {connection['packets']})"
            )

        connections_text = "\n".join(formatted_connections)
        prompt = (
            "You are a network security and traffic monitoring analyst. You are analyzing "
            "the active WAN traffic connections on a home router (LAN-to-LAN traffic has been "
            "filtered out).\n\n"
            "IP Address Roles:\n"
            "- Local/Internal IPs: 192.168.x.x, 10.x.x.x, 172.16.x.x through 172.31.x.x, and "
            "IPv6 local ranges starting with fe80, fc00, or fd00.\n"
            "- Public/External IPs: Any IP not matching the local ranges (e.g., 192.95.33.x is public/external).\n\n"
            "Connection notation format: [PROTOCOL] SOURCE -> DESTINATION:PORT\n"
            "- If SOURCE is a local IP and DESTINATION is an external IP, it is an OUTBOUND connection. "
            "The external host is listening on PORT. The local device is the client (not listening on that port).\n"
            "- If SOURCE is an external IP and DESTINATION is a local IP, it is an INBOUND connection. "
            "The local device is listening on PORT and accepting connections from the internet, which could "
            "indicate unintended exposure (e.g., if you see external_ip -> local_ip:443, the local device is hosting "
            "a service on 443).\n\n"
            "Here are the top active external connections sorted by bandwidth usage:\n"
            f"{connections_text}\n\n"
            "Analyze this active connection list and output a JSON object with:\n"
            '1. "summary": A high-level overview of the active traffic (e.g. which local devices '
            "are active, what services like HTTPS, DNS, or custom ports are heavily used, and "
            "overall health).\n"
            '2. "top_consumers": An array of objects, each describing a major traffic consumer. '
            "Ensure each local IP device appears at most ONCE in this array. If a device has multiple "
            "active connections, combine them into a single description detailing its total behavior:\n"
            '   - "device": The local IP address.\n'
            '   - "description": Summary of what that device is doing (destinations, ports/services, '
            "and combined bandwidth usage).\n"
            '3. "security_concerns": An array of objects for potential security issues (e.g., plain-text '
            "traffic like HTTP/FTP/Telnet, unexpected open ports on local IPs from inbound external traffic, or port "
            "scanning). Note: Standard outbound connections (local_ip -> external_ip:443/80/53) are NORMAL web "
            "traffic and should NOT be flagged as security concerns or unexpected exposures.\n"
            '   - "type": Type of security concern.\n'
            '   - "severity": One of "high", "medium", "low".\n'
            '   - "description": Detailed explanation of the risk.\n'
            '4. "recommendations": An array of actionable administration or firewall recommendations '
            '(or ["None"] if normal).\n\n'
            "Return ONLY the JSON object, no other text."
        )

        payload = {
            "model": OMLX_MODEL,
            "messages": [
                {"role": "system", "content": "You are a network traffic and security analyst."},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 2048,
            "temperature": 0.3,
        }

        import urllib.request

        req = urllib.request.Request(
            "http://127.0.0.1:8000/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer vUYmhvvVwRSwW58",
            },
            method="POST",
        )

        resp = urllib.request.urlopen(req, timeout=300)
        result = json.loads(resp.read().decode())
        content = result["choices"][0]["message"]["content"]

        decoder = json.JSONDecoder()
        start_index = content.find("{")
        if start_index == -1:
            raise ValueError("No JSON object found in response")

        try:
            parsed_object, _ignored_end_index = decoder.raw_decode(content[start_index:])
            if not isinstance(parsed_object, dict):
                raise ValueError(f"Expected dict, got {type(parsed_object).__name__}")

            # Post-process top_consumers to resolve IP addresses to hostnames
            top_consumers = parsed_object.get("top_consumers", [])
            if isinstance(top_consumers, list):
                for consumer in top_consumers:
                    if isinstance(consumer, dict) and "device" in consumer:
                        device_ip = consumer["device"]
                        resolved_name = resolve_ip(device_ip)
                        if resolved_name and resolved_name != device_ip:
                            consumer["device_name"] = resolved_name
                        else:
                            consumer["device_name"] = device_ip

            return jsonify(parsed_object)
        except json.JSONDecodeError as decode_error:
            raise ValueError(f"Failed to parse JSON: {decode_error}") from decode_error

    except Exception as error:
        return jsonify({"error": str(error)}), 500


@app.route("/api/analyze/cache")
def api_analyze_cache():
    """Return classification cache statistics."""
    return jsonify(get_cache_stats())


if __name__ == "__main__":
    app.run(debug=True, port=5001)
