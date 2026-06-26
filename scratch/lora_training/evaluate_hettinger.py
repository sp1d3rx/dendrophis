import json
import time

import requests

# Configuration
API_URL = "http://localhost:8000/v1/chat/completions"
API_KEY = "vUYmhvvVwRSwW58"
# The fused model name matches the directory name in LM Studio
MODEL_HETTINGER = "gemma-4-26B-A4B-it-hettinger-8bit"

CHALLENGES = [
    {"name": "Manual Index Loop", "code": "for i in range(len(mylist)):\n    print(i, mylist[i])"},
    {
        "name": "List Append Loop",
        "code": "results = []\nfor item in data:\n    if item > 10:\n        results.append(item * 2)",
    },
    {
        "name": "Dictionary Membership",
        "code": "if key in my_dict.keys():\n    value = my_dict[key]\nelse:\n    value = 'default'",
    },
    {"name": "String Concatenation", "code": "s = ''\nfor word in words:\n    s += word + ' '"},
    {"name": "Unnecessary Lambda", "code": "sorted_data = sorted(data, key=lambda x: x.priority)"},
    {
        "name": "Manual Pairwise",
        "code": "for i in range(len(items) - 1):\n    current_item = items[i]\n    next_item = items[i+1]\n    print(current_item, next_item)",  # noqa: E501
    },
]


def evaluate(model_name, adapter_path=None):
    print(f"\n--- Evaluating Model: {model_name} {'(with adapter)' if adapter_path else '(base)'} ---")

    for challenge in CHALLENGES:
        print(f"\n[Challenge: {challenge['name']}]")
        print(f"Input:\n{challenge['code']}")

        payload = {
            "model": model_name,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a senior Python core developer who follows Raymond Hettinger's advice. Refactor the code to be more idiomatic and efficient.",  # noqa: E501
                },
                {"role": "user", "content": f"Refactor this:\n\n{challenge['code']}"},
            ],
            "max_tokens": 300,
            "temperature": 0.1,
        }

        # Note: If we want to use the adapter, oMLX might need to be restarted with the adapter path
        # or we might use mlx_lm directly for evaluation.

        try:
            start_time = time.time()
            response = requests.post(API_URL, headers={"Authorization": f"Bearer {API_KEY}"}, json=payload)
            elapsed = time.time() - start_time

            if response.status_code == 200:
                result = response.json()
                try:
                    content = result["choices"][0]["message"]["content"]
                    print(f"\nRefactored:\n{content}")
                    print(
                        f"\n(Time: {elapsed:.2f}s, Tokens: {result['usage']['completion_tokens']}, TPS: {result['usage']['completion_tokens'] / elapsed:.2f})"  # noqa: E501
                    )
                except KeyError:
                    print(
                        f"KeyError: Could not find 'content' in response. Full response: {json.dumps(result, indent=2)}"
                    )
            else:
                print(f"Error: {response.status_code} - {response.text}")
        except Exception as e:
            print(f"Exception: {e}")


if __name__ == "__main__":
    evaluate(MODEL_HETTINGER)
