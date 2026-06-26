import json
import lzma
import sys
from pathlib import Path


def inspect_session(session_id):
    sessions_dir = Path.home() / ".config" / "dendrophis" / "sessions"
    files = sorted(sessions_dir.glob(f"session-{session_id}.*.json.xz"), reverse=True)
    if not files:
        print(f"No session file found for {session_id}")
        return

    file_path = files[0]
    print(f"Inspecting {file_path}")

    with lzma.open(file_path, "rt") as f:
        data = json.load(f)

    messages = data.get("messages", [])
    for i, msg in enumerate(messages):
        role = msg.get("role")
        content = msg.get("content", "")
        if isinstance(content, str) and "[File" in content:
            print(f"\n--- Message {i} ({role}) ---")
            print(repr(content[:200]))  # Show raw representation of first 200 chars


if __name__ == "__main__":
    if len(sys.argv) > 1:
        inspect_session(sys.argv[1])
    else:
        print("Usage: python inspect_session.py <session_id>")
