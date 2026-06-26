"""
Log classification module for routerdash.

Uses an LLM via omlx (OpenAI-compatible API) to classify syslog entries
into semantic categories, assign severity, and generate short summaries.
"""

import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.request

# --- Config (mirrors omlx.yaml) ---
OMLX_BASE_URL = "http://127.0.0.1:8000/v1"
OMLX_API_KEY = "vUYmhvvVwRSwW58"
OMLX_MODEL = "LFM2.5-8B-A1B-hettinger-8bit"
OMLX_TIMEOUT = 300.0
OMLX_TEMPERATURE = 0.3
OMLX_MAX_TOKENS = 2048
# Configure logger
logger = logging.getLogger("routerdash.analyze")
if not logger.handlers:
    console_handler = logging.StreamHandler()
    formatter = logging.Formatter("[%(asctime)s] %(levelname)s in %(module)s: %(message)s")
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    logger.setLevel(logging.INFO)

# Classification categories the LLM can assign
CATEGORIES = [
    "security",  # logins, auth failures, firewall events
    "network",  # interface state changes, DHCP, routing
    "dns",  # DNS queries, cache, forwards, replies
    "system",  # general system events, kernel, services
    "error",  # errors, crashes, failures
    "warning",  # warnings, degraded state
    "info",  # routine operational events
    "dhcp",  # DHCP lease events
]

# System prompt that tells the LLM how to classify
CLASSIFY_PROMPT = """\
You are a network log classifier for a home router. You will receive a JSON \
object with fields from a syslog entry. Classify it and return a JSON object \
with these fields:

- category: one of {categories}
- severity: one of "critical", "warning", "info", "debug"
- summary: a one-line plain-English summary of the event (15-40 words)
- tags: a list of 0-4 short keywords (lowercase, no spaces)

Rules:
- Be precise about what happened
- The summary should be useful to a human reading a network monitoring dashboard
- For DNS entries, mention the domain if present
- For network entries, mention the interface if present
- For security entries, mention the IP or user if present
- IMPORTANT: 192.168.x.x, 10.x.x.x, and 172.16-31.x.x are LOCAL/LAN IPs. \
Do NOT call them external. They are internal/local.
- IMPORTANT: "external" means an IP NOT in the private ranges above (e.g., 8.8.8.8, 1.1.1.1)
- Do NOT use the tag 'error' unless the log level is 'err' or the message indicates a failure.
- Use the "process" field (dnsmasq, netifd, uhttpd, etc.) for context
- Use the "level" field (err, warn, info, debug) to help determine severity
- If the log message is empty or unparseable, set category to "info", severity to "debug"

Example input:
{{"message": \
"accepted login on /admin for root from 192.168.1.129", \
"process": "uhttpd", "level": "info"}}
Example output:
{{"category": "security", "severity": "info", \
"summary": \
"Root login accepted from internal IP 192.168.1.129", \
"tags": ["login", "root", "uhttpd"]}}

Return ONLY the JSON object, no other text.
"""

# System prompt that tells the LLM how to classify a batch of logs
CLASSIFY_BATCH_PROMPT = """\
You are a network log classifier for a home router. You will receive a JSON \
list of objects, each representing a syslog entry with an "id" field. \
Classify each entry and return a JSON list of objects (same length as input), \
where each object has these fields:

- id: the "id" from the input entry
- category: one of {categories}
- severity: one of "critical", "warning", "info", "debug"
- summary: a one-line plain-English summary of the event (15-40 words)
- tags: a list of 0-4 short keywords (lowercase, no spaces)

Rules:
- Be precise about what happened
- The summary should be useful to a human reading a network monitoring dashboard
- For DNS entries, mention the domain if present
- For network entries, mention the interface if present
- For security entries, mention the IP or user if present
- IMPORTANT: 192.168.x.x, 10.x.x.x, and 172.16-31.x.x are LOCAL/LAN IPs. \
Do NOT call them external. They are internal/local.
- IMPORTANT: "external" means an IP NOT in the private ranges above (e.g., 8.8.8.8, 1.1.1.1)
- Do NOT use the tag 'error' unless the log level is 'err' or the message indicates a failure.
- Use the "process" field (dnsmasq, netifd, uhttpd, etc.) for context
- Use the "level" field (err, warn, info, debug) to help determine severity
- If a log message is empty or unparseable, set category to "info", severity to "debug"

Example input:
[
  {{"id": 0, "message": \
"accepted login on /admin for root from 192.168.1.129", \
"process": "uhttpd", "level": "info"}},
  {{"id": 1, "message": \
"query[A] google.com from 192.168.1.120", \
"process": "dnsmasq", "level": "info"}}
]
Example output:
[
  {{"id": 0, "category": "security", "severity": "info", \
"summary": \
"Root login accepted from internal IP 192.168.1.129", \
"tags": ["login", "root", "uhttpd"]}},
  {{"id": 1, "category": "dns", "severity": "info", \
"summary": \
"DNS query for google.com from internal IP 192.168.1.120", \
"tags": ["dns", "query", "google.com"]}}
]

Return ONLY the JSON list of objects, no other text.
"""

# Batch size configuration
LLM_BATCH_SIZE = 10

# Cache configuration
_classification_cache: dict[str, dict] = {}
_cache_ttl = 3600  # 1 hour
CACHE_FILE = os.path.join(os.path.dirname(__file__), ".classification_cache.json")


def _load_cache():
    global _classification_cache
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as file_handle:
                _classification_cache = json.load(file_handle)
        except Exception:
            _classification_cache = {}
    else:
        _classification_cache = {}


def _save_cache():
    try:
        with open(CACHE_FILE, "w") as file_handle:
            json.dump(_classification_cache, file_handle, indent=2)
    except Exception:
        pass


# Load cache at module startup
_load_cache()


def _extract_json(text: str) -> str:
    """Extract JSON from LLM output that may contain free-text reasoning.

    Handles:
    - Pure JSON (return as-is)
    - JSON in markdown code blocks
    - JSON in <json> tags
    - JSON list embedded in free-text (find first [ ... last ])
    - JSON object embedded in free-text (find first { ... last })
    """
    cleaned_text = text.strip()

    # Try direct parse first
    try:
        json.loads(cleaned_text)
        return cleaned_text
    except json.JSONDecodeError:
        pass

    # Try markdown code blocks
    if "```" in cleaned_text:
        code_block_parts = cleaned_text.split("```")
        if len(code_block_parts) >= 3:
            block_content = code_block_parts[1].strip()
            if block_content.startswith("json"):
                block_content = block_content[4:].strip()
            try:
                json.loads(block_content)
                return block_content
            except json.JSONDecodeError:
                pass

    # Try <json> tags
    if "<json>" in cleaned_text:
        tag_parts = cleaned_text.split("<json>")
        for tag_part in tag_parts:
            closing_parts = tag_part.split("</json>")
            if len(closing_parts) >= 2:
                candidate_content = closing_parts[0].strip()
                try:
                    json.loads(candidate_content)
                    return candidate_content
                except json.JSONDecodeError:
                    pass

    # Find first [ ... last ] (list) and first { ... last } (object)
    first_bracket = cleaned_text.find("[")
    last_bracket = cleaned_text.rfind("]")
    first_brace = cleaned_text.find("{")
    last_brace = cleaned_text.rfind("}")

    has_list = first_bracket != -1 and last_bracket != -1 and first_bracket < last_bracket
    has_object = first_brace != -1 and last_brace != -1 and first_brace < last_brace

    if has_list and has_object:
        if first_bracket < first_brace:
            # List starts first, try list then object
            candidate_list = cleaned_text[first_bracket : last_bracket + 1]
            try:
                json.loads(candidate_list)
                return candidate_list
            except json.JSONDecodeError:
                pass

            candidate_object = cleaned_text[first_brace : last_brace + 1]
            try:
                json.loads(candidate_object)
                return candidate_object
            except json.JSONDecodeError:
                pass
        else:
            # Object starts first, try object then list
            candidate_object = cleaned_text[first_brace : last_brace + 1]
            try:
                json.loads(candidate_object)
                return candidate_object
            except json.JSONDecodeError:
                pass

            candidate_list = cleaned_text[first_bracket : last_bracket + 1]
            try:
                json.loads(candidate_list)
                return candidate_list
            except json.JSONDecodeError:
                pass
    elif has_list:
        candidate_list = cleaned_text[first_bracket : last_bracket + 1]
        try:
            json.loads(candidate_list)
            return candidate_list
        except json.JSONDecodeError:
            pass
    elif has_object:
        candidate_object = cleaned_text[first_brace : last_brace + 1]
        try:
            json.loads(candidate_object)
            return candidate_object
        except json.JSONDecodeError:
            pass

    # Last resort: try finding matching brace/bracket pairs and validate each one
    # Try brace pairs (objects)
    brace_stack = []
    brace_pairs = []
    for char_index, character in enumerate(cleaned_text):
        if character == "{":
            brace_stack.append(char_index)
        elif character == "}" and brace_stack:
            start_index = brace_stack.pop()
            brace_pairs.append((start_index, char_index))

    # Try each brace pair, prefer shorter (more precise) matches first
    brace_pairs.sort(key=lambda pair_tuple: pair_tuple[1] - pair_tuple[0])
    for start_index, end_index in brace_pairs:
        candidate_object = cleaned_text[start_index : end_index + 1]
        try:
            json.loads(candidate_object)
            return candidate_object
        except json.JSONDecodeError:
            pass

    # Try bracket pairs (lists)
    bracket_stack = []
    bracket_pairs = []
    for char_index, character in enumerate(cleaned_text):
        if character == "[":
            bracket_stack.append(char_index)
        elif character == "]" and bracket_stack:
            start_index = bracket_stack.pop()
            bracket_pairs.append((start_index, char_index))

    bracket_pairs.sort(key=lambda pair_tuple: pair_tuple[1] - pair_tuple[0])
    for start_index, end_index in bracket_pairs:
        candidate_list = cleaned_text[start_index : end_index + 1]
        try:
            json.loads(candidate_list)
            return candidate_list
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError("Could not extract JSON from LLM output", cleaned_text, 0)


def _classify_single(entry_data: dict) -> dict:
    """Send a single log entry to the LLM for classification.

    Args:
        entry_data: Dict with keys like 'message', 'process', 'level', 'date', etc.
    """
    message_text = entry_data.get("message", "")
    logger.info(f"Sending single log entry to LLM for classification: '{message_text[:50]}'")
    prompt = CLASSIFY_PROMPT.format(categories=CATEGORIES)

    # Build a simplified view for the LLM - just the fields it needs
    llm_input = {
        "message": entry_data.get("message", ""),
        "process": entry_data.get("process", ""),
        "level": entry_data.get("level", ""),
        "date": entry_data.get("date", ""),
        "facility": entry_data.get("facility", ""),
    }

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(llm_input)},
    ]

    payload = {
        "model": OMLX_MODEL,
        "messages": messages,
        "max_tokens": OMLX_MAX_TOKENS,
        "temperature": OMLX_TEMPERATURE,
    }

    data = json.dumps(payload).encode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OMLX_API_KEY}",
    }

    req = urllib.request.Request(
        OMLX_BASE_URL + "/chat/completions",
        data=data,
        headers=headers,
        method="POST",
    )

    resp = urllib.request.urlopen(req, timeout=OMLX_TIMEOUT)
    result = json.loads(resp.read().decode())

    content = result["choices"][0]["message"]["content"]
    logger.info(f"Raw single LLM response: {content.strip()}")
    decoder = json.JSONDecoder()
    start_index = content.find("{")
    if start_index == -1:
        raise ValueError("No JSON object found in response")
    try:
        parsed_object, _ignored_end_index = decoder.raw_decode(content[start_index:])
        if not isinstance(parsed_object, dict):
            raise ValueError(f"Expected dict, got {type(parsed_object).__name__}")
        return parsed_object
    except json.JSONDecodeError as decode_error:
        raise ValueError(f"Failed to parse JSON: {decode_error}") from decode_error


def _classify_batch_llm(entries_batch: list[dict]) -> list[dict]:
    """Send a batch of log entries to the LLM for classification.

    Args:
        entries_batch: List of dicts with keys like 'message', 'process', 'level', etc.
    """
    logger.info(f"Sending batch request to LLM (model: {OMLX_MODEL}) with {len(entries_batch)} entries.")
    batch_prompt = CLASSIFY_BATCH_PROMPT.format(categories=CATEGORIES)

    # Build simplified views with unique ID (index)
    simplified_inputs = []
    for entry_index, entry_data in enumerate(entries_batch):
        simplified_inputs.append(
            {
                "id": entry_index,
                "message": entry_data.get("message", ""),
                "process": entry_data.get("process", ""),
                "level": entry_data.get("level", ""),
                "date": entry_data.get("date", ""),
                "facility": entry_data.get("facility", ""),
            }
        )

    messages_payload = [
        {"role": "system", "content": batch_prompt},
        {"role": "user", "content": json.dumps(simplified_inputs)},
    ]

    api_payload = {
        "model": OMLX_MODEL,
        "messages": messages_payload,
        "max_tokens": OMLX_MAX_TOKENS,
        "temperature": OMLX_TEMPERATURE,
    }

    encoded_data = json.dumps(api_payload).encode()
    headers_config = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OMLX_API_KEY}",
    }

    api_request = urllib.request.Request(
        OMLX_BASE_URL + "/chat/completions",
        data=encoded_data,
        headers=headers_config,
        method="POST",
    )

    api_response = urllib.request.urlopen(api_request, timeout=OMLX_TIMEOUT)
    response_data = json.loads(api_response.read().decode())

    response_content = response_data["choices"][0]["message"]["content"]
    logger.info(f"Received raw LLM response:\n{response_content.strip()}")

    # Extract all JSON objects using the robust raw_decode method
    classifications_result = []
    decoder = json.JSONDecoder()
    decode_index = 0
    while decode_index < len(response_content):
        start_index = response_content.find("{", decode_index)
        if start_index == -1:
            break
        try:
            parsed_object, end_index = decoder.raw_decode(response_content[start_index:])
            if isinstance(parsed_object, dict):
                classifications_result.append(parsed_object)
            decode_index = start_index + end_index
        except json.JSONDecodeError:
            decode_index = start_index + 1

    logger.info(f"Extracted {len(classifications_result)} JSON objects from batch response using raw_decode.")

    # Map the classifications back to the inputs using "id"
    aligned_classifications = [None] * len(entries_batch)

    for item in classifications_result:
        if isinstance(item, dict) and "id" in item:
            try:
                item_id = int(item["id"])
                if 0 <= item_id < len(entries_batch):
                    aligned_classifications[item_id] = {
                        "category": item.get("category", "info"),
                        "severity": item.get("severity", "info"),
                        "summary": item.get("summary", ""),
                        "tags": item.get("tags", []),
                    }
            except (ValueError, TypeError):
                pass

    aligned_count = sum(1 for item in aligned_classifications if item is not None)
    logger.info(f"Successfully aligned {aligned_count} of {len(entries_batch)} entries from batch LLM response.")
    return aligned_classifications


def _classify_uncached(entries: list[dict]) -> None:
    """Classify a list of entries, leveraging cache and batching uncached ones.

    Updates entries in place with classification keys.
    """
    total_entries = len(entries)
    logger.info(f"Starting _classify_uncached on {total_entries} log entries.")
    now_timestamp = time.time()
    uncached_indices = []
    uncached_hashes = []
    uncached_entries = []

    for entry_index, entry in enumerate(entries):
        message = entry.get("message", "")
        process = entry.get("process", "")
        level = entry.get("level", "")
        message_hash = hashlib.md5(f"{message}|{process}|{level}".encode()).hexdigest()

        # Check cache
        cached_entry = _classification_cache.get(message_hash)
        if cached_entry and now_timestamp < cached_entry.get("_expires", 0):
            # Already cached, update entry directly
            entry.update(
                {
                    "category": cached_entry.get("category", "info"),
                    "severity": cached_entry.get("severity", "info"),
                    "summary": cached_entry.get("summary", ""),
                    "tags": cached_entry.get("tags", []),
                }
            )
        else:
            uncached_indices.append(entry_index)
            uncached_hashes.append(message_hash)
            uncached_entries.append(entry)

    # Process uncached in batches
    number_of_uncached = len(uncached_entries)
    logger.info(f"Cache check: {total_entries - number_of_uncached} hits, {number_of_uncached} uncached entries.")

    for batch_start in range(0, number_of_uncached, LLM_BATCH_SIZE):
        batch_slice = uncached_entries[batch_start : batch_start + LLM_BATCH_SIZE]
        batch_hashes = uncached_hashes[batch_start : batch_start + LLM_BATCH_SIZE]
        batch_original_indices = uncached_indices[batch_start : batch_start + LLM_BATCH_SIZE]

        logger.info(
            f"Classifying batch of {len(batch_slice)} entries "
            f"(uncached indices {batch_start} to {batch_start + len(batch_slice) - 1})."
        )

        try:
            # Try to classify the batch in a single LLM request
            batch_classifications = _classify_batch_llm(batch_slice)
            for item_index, classification in enumerate(batch_classifications):
                message_hash = batch_hashes[item_index]
                original_index = batch_original_indices[item_index]

                if classification is None:
                    # Omitted by LLM in the batch response, fall back to individual classification
                    logger.warning(
                        f"Log entry (index {item_index} in batch) was omitted by batch. "
                        f"Falling back to single classification."
                    )
                    try:
                        classification = _classify_single(batch_slice[item_index])
                    except Exception as fallback_error:
                        logger.error(f"Fallback classification failed: {fallback_error}")
                        classification = {
                            "category": "info",
                            "severity": "debug",
                            "summary": "Log entry could not be classified.",
                            "tags": [],
                        }

                # Update cache
                _classification_cache[message_hash] = {
                    **classification,
                    "_expires": time.time() + _cache_ttl,
                }
                # Update entry
                entries[original_index].update(classification)

        except Exception as batch_error:
            # Fallback to classifying each entry in the failed batch individually
            logger.error(
                f"Batch classification failed with error: {batch_error}. "
                f"Falling back to single classifications for the entire batch."
            )
            for item_index, failed_entry in enumerate(batch_slice):
                message_hash = batch_hashes[item_index]
                original_index = batch_original_indices[item_index]
                try:
                    single_classification = _classify_single(failed_entry)
                except Exception as single_error:
                    logger.error(f"Single classification failed for log index {item_index}: {single_error}")
                    single_classification = {
                        "category": "info",
                        "severity": "debug",
                        "summary": "Log entry could not be classified.",
                        "tags": [],
                    }
                # Update cache
                _classification_cache[message_hash] = {
                    **single_classification,
                    "_expires": time.time() + _cache_ttl,
                }
                # Update entry
                entries[original_index].update(single_classification)

    if number_of_uncached > 0:
        _save_cache()


def classify_entry(entry: dict) -> dict:
    """
    Classify a single log entry.

    Uses a hash-based cache so repeated classification of the same entry
    (e.g. after page reloads) doesn't hit the LLM.

    Args:
        entry: A parsed log entry dict with at least a 'message' key.

    Returns:
        Dict with keys: category, severity, summary, tags
    """
    # Hash based on message + process + level (same content = same classification)
    message = entry.get("message", "")
    process = entry.get("process", "")
    level = entry.get("level", "")
    message_hash = hashlib.md5(f"{message}|{process}|{level}".encode()).hexdigest()

    # Check cache
    cached = _classification_cache.get(message_hash)
    if cached and time.time() < cached.get("_expires", 0):
        return {key: value for key, value in cached.items() if key != "_expires"}

    # Classify
    try:
        result = _classify_single(entry)
    except Exception:
        result = {
            "category": "info",
            "severity": "debug",
            "summary": "Log entry could not be classified.",
            "tags": [],
        }

    # Cache
    _classification_cache[message_hash] = {
        **result,
        "_expires": time.time() + _cache_ttl,
    }
    _save_cache()

    return {
        "category": result.get("category", "info"),
        "severity": result.get("severity", "info"),
        "summary": result.get("summary", ""),
        "tags": result.get("tags", []),
    }


def classify_entries(entries: list[dict]) -> list[dict]:
    """
    Classify a list of log entries, enriching each with classification data.

    Args:
        entries: List of parsed log entry dicts.

    Returns:
        Same list with 'category', 'severity', 'summary', and 'tags' added to each.
    """
    _classify_uncached(entries)
    return entries


def classify_batch(entries: list[dict]) -> list[dict]:
    """
    Classify a list of log entries in batches.

    Args:
        entries: List of parsed log entry dicts.

    Returns:
        Same list with 'category', 'severity', 'summary', and 'tags' added to each.
    """
    _classify_uncached(entries)
    return entries


def clear_cache():
    """Clear the classification cache."""
    _classification_cache.clear()
    _save_cache()


def get_cache_stats() -> dict:
    """Return cache statistics."""
    now = time.time()
    active = sum(1 for v in _classification_cache.values() if now < v.get("_expires", 0))
    return {
        "total": len(_classification_cache),
        "active": active,
        "ttl": _cache_ttl,
    }
