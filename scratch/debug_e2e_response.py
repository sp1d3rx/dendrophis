import urllib.request
import json

url = "http://127.0.0.1:8000/v1/chat/completions"
headers = {"Authorization": "Bearer vUYmhvvVwRSwW58", "Content-Type": "application/json"}

# Construct the same system prompt with tool injection that LLMClient builds
tool_defs = json.dumps(
    {
        "type": "function",
        "function": {
            "name": "replace_file_content",
            "description": "Replace content in a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "TargetFile": {"type": "string"},
                    "TargetContent": {"type": "string"},
                    "ReplacementContent": {"type": "string"},
                    "StartLine": {"type": "integer"},
                    "EndLine": {"type": "integer"},
                },
                "required": ["TargetFile", "TargetContent", "ReplacementContent", "StartLine", "EndLine"],
            },
        },
    }
)

system_prompt = f"""You are a helpful coding assistant. You have access to tools.
You are provided with function signatures within <tools></tools> XML tags:
<tools>
{tool_defs}
</tools>

To call a function, you must output a tool call using the following format:
<tool_call>
{{"name": "function_name", "arguments": {{"arg1": "value1"}}}}
</tool_call>
"""

user_prompt = "Add a new function `double(x)` to /Users/derekw/Documents/projects/boiga/tests/test_cw_temp.py that returns x * 2. Keep the existing old_func function unchanged."

payload = {
    "model": "Qwen3.6-35B-A3B-OptiQ-4bit",
    "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
    "temperature": 0.1,
    "max_tokens": 4096,
}

req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
try:
    response = urllib.request.urlopen(req)
    result = json.loads(response.read().decode())
    print(json.dumps(result, indent=2))
except Exception as e:
    print(f"Error: {e}")
