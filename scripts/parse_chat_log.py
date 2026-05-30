#!/usr/bin/env python3
"""Parse dendrophis chat log and summarize requests/responses."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def parse_log(path: str) -> list[dict]:
    """Parse log into a list of entries with direction, timestamp, and payload."""
    text = Path(path).read_text()
    entries = []
    pattern = re.compile(
        r"\[(\d{2}:\d{2}:\d{2}\.\d+)\] (CLIENT → SERVER|SERVER → CLIENT)\n(.*?)(?=\n\[|\Z)",
        re.DOTALL,
    )
    for m in pattern.finditer(text):
        ts, direction, body = m.group(1), m.group(2), m.group(3).strip()
        try:
            decoder = json.JSONDecoder()
            payload, _ = decoder.raw_decode(body)
        except (json.JSONDecodeError, ValueError):
            payload = body
        entries.append({"ts": ts, "direction": direction, "payload": payload})
    return entries


def summarize_messages(messages: list[dict]) -> None:
    for i, m in enumerate(messages):
        role = m.get("role", "?")
        tc = m.get("tool_calls")
        tc_id = m.get("tool_call_id", "")
        name = m.get("name", "")
        if tc:
            for t in tc:
                fn = t.get("function", {})
                print(f"  [{i}] {role}: tool_call id={t.get('id')} name={fn.get('name')}")
        elif role == "tool":
            content = str(m.get("content", ""))[:60].replace("\n", " ")
            print(f"  [{i}] {role}: tool_call_id={tc_id} name={name} | {content}")
        else:
            content = str(m.get("content") or "")[:80].replace("\n", " ")
            print(f"  [{i}] {role}: {content}")


def main(log_path: str, mode: str = "requests") -> None:
    entries = parse_log(log_path)
    requests = [e for e in entries if e["direction"] == "CLIENT → SERVER"]
    responses = [e for e in entries if e["direction"] == "SERVER → CLIENT"]

    if mode == "requests":
        print(f"Found {len(requests)} requests\n")
        for idx, req in enumerate(requests):
            p = req["payload"]
            if not isinstance(p, dict):
                print(f"Request #{idx+1} [{req['ts']}]: (non-JSON)")
                continue
            msgs = p.get("messages", [])
            model = p.get("model", "?")
            print(f"Request #{idx+1} [{req['ts']}] model={model} messages={len(msgs)}:")
            summarize_messages(msgs)
            print()

    elif mode == "tool_calls":
        # Show only tool call chunks from server responses
        print("Tool calls from server responses:\n")
        for e in responses:
            p = e["payload"]
            if not isinstance(p, dict):
                continue
            for choice in p.get("choices", []):
                delta = choice.get("delta", {})
                for tc in delta.get("tool_calls") or []:
                    fn = tc.get("function", {})
                    print(f"  [{e['ts']}] id={tc.get('id')} name={fn.get('name')} args={fn.get('arguments','')[:60]}")

    elif mode == "duplicates":
        # Find requests with duplicate consecutive assistant messages
        print("Checking for duplicate assistant messages in requests:\n")
        for idx, req in enumerate(requests):
            p = req["payload"]
            if not isinstance(p, dict):
                continue
            msgs = p.get("messages", [])
            for i in range(1, len(msgs)):
                prev, cur = msgs[i - 1], msgs[i]
                if prev.get("role") == "assistant" and cur.get("role") == "assistant":
                    print(f"Request #{idx+1} [{req['ts']}]: consecutive assistant msgs at [{i-1}] and [{i}]")
                    summarize_messages([prev, cur])
                    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <log_path> [requests|tool_calls|duplicates]")
        sys.exit(1)
    log_path = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "requests"
    main(log_path, mode)
