import json
from pathlib import Path

from datasets import load_dataset


def prepare_data():
    print("🚀 Starting MagicCoder dataset preparation for Python LoRA...")

    # 1. Create directory structure
    data_dir = Path("scratch/lora_training/data")
    data_dir.mkdir(parents=True, exist_ok=True)

    # 2. Load the dataset
    # We use the Evol-Instruct-110K version as it has more complex logic
    print("📦 Downloading ise-uiuc/Magicoder-Evol-Instruct-110K (this may take a minute)...")
    dataset = load_dataset("ise-uiuc/Magicoder-Evol-Instruct-110K", split="train")

    # 3. Filter for Python and "Hettinger-style" keywords
    print("🔍 Filtering for high-quality Python refactoring examples...")
    python_examples = []

    # 3.1 Inject "Hettinger Gold" — canonical idiomatic patterns
    hettinger_gold = [
        {
            "instruction": "Refactor this loop to be more idiomatic: for i in range(len(names)): print(i, names[i])",
            "response": "Use `enumerate()` for a cleaner, more Pythonic approach:\n\n```python\nfor i, name in enumerate(names):\n    print(i, name)\n```",  # noqa: E501
        },
        {
            "instruction": "How do I loop over two collections of the same length efficiently?",
            "response": "Use `zip()` to iterate over multiple sequences in parallel:\n\n```python\nfor name, color in zip(names, colors):\n    print(name, '-->', color)\n```",  # noqa: E501
        },
        {
            "instruction": "Implement a high-performance word frequency counter in Python.",
            "response": "Use `collections.Counter` for the most efficient and readable implementation:\n\n```python\nfrom collections import Counter\ncounts = Counter(words_list)\n# To get the top 10:\n# top_ten = counts.most_common(10)\n```",  # noqa: E501
        },
        {
            "instruction": "Implement the 'grouper' recipe from the itertools documentation.",
            "response": 'The `grouper` recipe is the canonical way to iterate over data in fixed-length chunks:\n\n```python\nfrom itertools import zip_longest\n\ndef grouper(iterable, n, fillvalue=None):\n    """Collect data into fixed-length chunks or blocks"""\n    # grouper(\'ABCDEFG\', 3, \'x\') --> ABC DEF Gxx\n    args = [iter(iterable)] * n\n    return zip_longest(*args, fillvalue=fillvalue)\n```',  # noqa: E501
        },
        {
            "instruction": "What is the idiomatic way to handle multiple dictionaries as a single mapping?",
            "response": "Use `collections.ChainMap` to group multiple dictionaries or other mappings together to create a single, updateable view:\n\n```python\nfrom collections import ChainMap\ncombined = ChainMap(dict_a, dict_b, dict_c)\n```",  # noqa: E501
        },
    ]

    for gold in hettinger_gold:
        formatted_text = f"### Instruction:\n{gold['instruction']}\n\n### Response:\n{gold['response']}"
        # We inject multiple copies of Gold data to increase its weight in the LoRA
        python_examples.extend({"text": formatted_text} for _ in range(50))

    for entry in dataset:
        instruction = entry["instruction"].lower()
        response = entry["response"]

        # We want Python-specific entries that are likely to teach better coding patterns
        if "python" in instruction:
            # Boost entries that specifically mention refactoring or optimization
            # Format for MLX-LM (OpenAI Chat format is often better for -it models)
            # but raw text with delimiters is standard for lora.py
            formatted_text = f"### Instruction:\n{entry['instruction']}\n\n### Response:\n{response}"

            python_examples.append({"text": formatted_text})

    print(f"✅ Found {len(python_examples)} relevant Python examples.")

    # 4. Shuffle and Split (95% train, 5% valid)
    import random

    random.seed(42)
    random.shuffle(python_examples)

    split_idx = int(len(python_examples) * 0.95)
    train_data = python_examples[:split_idx]
    valid_data = python_examples[split_idx:]

    # 5. Save to JSONL
    print(f"💾 Saving {len(train_data)} training and {len(valid_data)} validation samples...")

    with open(data_dir / "train.jsonl", "w") as f:
        for item in train_data:
            f.write(json.dumps(item) + "\n")

    with open(data_dir / "valid.jsonl", "w") as f:
        for item in valid_data:
            f.write(json.dumps(item) + "\n")

    print(f"✨ Preparation complete! Data is ready in: {data_dir.absolute()}")
    print("\nNext step: Run the MLX-LM LoRA command.")


if __name__ == "__main__":
    prepare_data()
