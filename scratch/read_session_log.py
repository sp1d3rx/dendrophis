import lzma
import json
from pathlib import Path

session_dir = Path.home() / ".config" / "dendrophis" / "sessions"
matches = sorted(session_dir.glob("session-b920bb48*.json*"), key=lambda f: f.stat().st_mtime, reverse=True)

if not matches:
    print("No session file found for b920bb48")
else:
    session_file = matches[0]
    print(f"Reading: {session_file}")
    
    if session_file.suffix == ".xz":
        with lzma.open(session_file, "rt", encoding="utf-8") as f:
            data = json.load(f)
    else:
        with open(session_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            
    messages = data.get("messages", [])
    print(f"Total messages: {len(messages)}")
    
    # Print detail of assistant messages
    for i, msg in enumerate(messages):
        if msg.get("role") == "assistant":
            print(f"\n=================== MESSAGE {i} ===================")
            print(json.dumps(msg, indent=2))
