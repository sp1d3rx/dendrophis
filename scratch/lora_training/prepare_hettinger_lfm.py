import json
import random
from pathlib import Path

from datasets import load_dataset


def prepare_data():
    print("🚀 Starting dataset preparation for LFM2.5 Python LoRA...")

    # 1. Create directory structure
    data_directory = Path("scratch/lora_training/data_lfm")
    data_directory.mkdir(parents=True, exist_ok=True)

    # 2. Load the dataset
    print("📦 Downloading ise-uiuc/Magicoder-Evol-Instruct-110K...")
    # Loading dataset from Hugging Face
    dataset = load_dataset("ise-uiuc/Magicoder-Evol-Instruct-110K", split="train")

    # 3. Filter for Python and Raymond Hettinger style Python coding
    print("🔍 Filtering and formatting examples...")
    python_examples = []

    # 3.1 Hettinger Gold examples (idiomatic patterns)
    hettinger_gold = [
        {
            "instruction": "Refactor this loop to be more pythonic: for i in range(len(names)): print(i, names[i])",
            "response": "Use `enumerate()` for a cleaner, more Pythonic approach that avoids manual indexing:\n\n```python\nfor index, name in enumerate(names):\n    print(index, name)\n```",  # noqa: E501
        },
        {
            "instruction": "How do I loop over two collections of the same length efficiently?",
            "response": "Use `zip()` to iterate over multiple sequences in parallel. This is cleaner and faster than using an index loop:\n\n```python\nfor name, color in zip(names, colors):\n    print(name, '-->', color)\n```",  # noqa: E501
        },
        {
            "instruction": "Implement a high-performance word frequency counter in Python.",
            "response": "Use `collections.Counter` from the standard library for the most efficient and readable implementation:\n\n```python\nfrom collections import Counter\n\ncounts = Counter(words_list)\n# To get the top 10 most common words:\n# top_ten = counts.most_common(10)\n```",  # noqa: E501
        },
        {
            "instruction": "Implement the 'grouper' recipe from the itertools documentation.",
            "response": 'The `grouper` recipe is the canonical way to iterate over data in fixed-length chunks using `zip_longest`:\n\n```python\nfrom itertools import zip_longest\n\ndef grouper(iterable, chunk_size, fillvalue=None):\n    """Collect data into fixed-length chunks or blocks"""\n    # grouper(\'ABCDEFG\', 3, \'x\') --> ABC DEF Gxx\n    iterators = [iter(iterable)] * chunk_size\n    return zip_longest(*iterators, fillvalue=fillvalue)\n```',  # noqa: E501
        },
        {
            "instruction": "What is the idiomatic way to handle multiple dictionaries as a single mapping?",
            "response": "Use `collections.ChainMap` to group multiple dictionaries or mappings together. It creates a single, updateable view without copying the underlying data, which is highly efficient:\n\n```python\nfrom collections import ChainMap\n\ncombined_mappings = ChainMap(dict_a, dict_b, dict_c)\n```",  # noqa: E501
        },
        {
            "instruction": "Refactor this to be more pythonic:\nif key in my_dict.keys():\n    value = my_dict[key]\nelse:\n    value = 'default'",  # noqa: E501
            "response": "First, avoid calling `.keys()` as membership testing on a dictionary is already highly optimized on the dictionary itself. Second, use `.get()` to simplify the retrieval with a default value in a single, atomic operation:\n\n```python\nvalue = my_dict.get(key, 'default')\n```",  # noqa: E501
        },
        {
            "instruction": "Refactor this dictionary grouping code:\n\n```python\ngroups = {}\nfor name, color in data:\n    if name not in groups:\n        groups[name] = []\n    groups[name].append(color)\n```",  # noqa: E501
            "response": "Use `collections.defaultdict` to automatically initialize the list for new keys. This makes the code cleaner, faster, and more declarative:\n\n```python\nfrom collections import defaultdict\n\ngroups = defaultdict(list)\nfor name, color in data:\n    groups[name].append(color)\n```",  # noqa: E501
        },
        {
            "instruction": "Refactor this list creation to be more pythonic:\n\n```python\nresults = []\nfor item in data:\n    if item > 10:\n        results.append(item * 2)\n```",  # noqa: E501
            "response": "Use a list comprehension. It is faster, more concise, and is the standard idiomatic way in Python to filter and map a collection in one step:\n\n```python\nresults = [item * 2 for item in data if item > 10]\n```",  # noqa: E501
        },
        {
            "instruction": "Refactor this string concatenation loop:\n\n```python\ns = ''\nfor word in words:\n    s += word + ' '\n```",  # noqa: E501
            "response": "Do not concatenate strings using `+` in a loop, as strings are immutable and this leads to quadratic time complexity O(N^2) due to repeated copying. Use `str.join()` instead for O(N) performance and cleaner code:\n\n```python\ns = ' '.join(words)\n```",  # noqa: E501
        },
        {
            "instruction": "Refactor this sort key:\n\n```python\nsorted_data = sorted(data, key=lambda x: x.priority)\n```",  # noqa: E501
            "response": "Avoid using a lambda function for simple attribute extraction. Using `operator.attrgetter` (or `operator.itemgetter` for dictionaries/tuples) is cleaner, faster, and more explicit:\n\n```python\nfrom operator import attrgetter\n\nsorted_data = sorted(data, key=attrgetter('priority'))\n```",  # noqa: E501
        },
        {
            "instruction": "Refactor this loop checking if any element is negative:\n\n```python\nhas_negative = False\nfor number in numbers:\n    if number < 0:\n        has_negative = True\n        break\n```",  # noqa: E501
            "response": "Use the built-in `any()` function combined with a generator expression. This is highly optimized, short, readable, and stops evaluating as soon as a match is found:\n\n```python\nhas_negative = any(number < 0 for number in numbers)\n```",  # noqa: E501
        },
        {
            "instruction": "Implement the 'unique_everseen' recipe from itertools to yield unique elements preserving order.",  # noqa: E501
            "response": 'Here is the canonical implementation of `unique_everseen` using `itertools.filterfalse` to efficiently track seen elements:\n\n```python\nfrom itertools import filterfalse\n\ndef unique_everseen(iterable, key=None):\n    """List unique elements, preserving order. Remember all elements ever seen."""\n    seen_elements = set()\n    add_seen = seen_elements.add\n    if key is None:\n        for element in filterfalse(seen_elements.__contains__, iterable):\n            add_seen(element)\n            yield element\n    else:\n        for element in iterable:\n            evaluated_key = key(element)\n            if evaluated_key not in seen_elements:\n                add_seen(evaluated_key)\n                yield element\n```',  # noqa: E501
        },
        {
            "instruction": "Implement the 'powerset' recipe using itertools combinations.",
            "response": 'The `powerset` recipe generates all possible subsets (subsequences) of an iterable:\n\n```python\nfrom itertools import chain, combinations\n\ndef powerset(iterable):\n    """powerset([1,2,3]) --> () (1,) (2,) (3,) (1,2) (1,3) (2,3) (1,2,3)"""\n    elements_list = list(iterable)\n    return chain.from_iterable(\n        combinations(elements_list, combination_size)\n        for combination_size in range(len(elements_list) + 1)\n    )\n```',  # noqa: E501
        },
        {
            "instruction": "Implement the 'roundrobin' recipe to consume multiple iterables in a round-robin sequence.",
            "response": "The `roundrobin` recipe yields elements from each iterable in turn until all are exhausted:\n\n```python\nfrom itertools import cycle, islice\n\ndef roundrobin(*iterables):\n    \"\"\"roundrobin('ABC', 'D', 'EF') --> A D E B F C\"\"\"\n    active_count = len(iterables)\n    next_functions = cycle(iter(iterator).__next__ for iterator in iterables)\n    while active_count:\n        try:\n            for next_func in next_functions:\n                yield next_func()\n        except StopIteration:\n            active_count -= 1\n            next_functions = cycle(islice(next_functions, active_count))\n```",  # noqa: E501
        },
        {
            "instruction": "Implement the 'tail' recipe to get the last n items of an iterable efficiently.",
            "response": 'The `tail` recipe uses `collections.deque` with `maxlen` to efficiently extract the final items:\n\n```python\nfrom collections import deque\n\ndef tail(number_of_items, iterable):\n    """Return an iterator over the last number_of_items items."""\n    # tail(3, \'ABCDEFG\') --> E F G\n    return iter(deque(iterable, maxlen=number_of_items))\n```',  # noqa: E501
        },
    ]

    # Format the Gold examples with standard ChatML system prompt
    system_prompt = "You are a senior Python core developer who follows Raymond Hettinger's advice. Refactor the code to be more idiomatic and efficient."  # noqa: E501

    for gold_example in hettinger_gold:
        prompt_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": gold_example["instruction"]},
            {"role": "assistant", "content": gold_example["response"]},
        ]
        # Weight Gold data by repeating it 100 times to influence the model strongly
        python_examples.extend({"messages": prompt_messages} for _ in range(100))

    # Filter the Evol-Instruct dataset for python-related instructions
    for dataset_row in dataset:
        instruction_text = dataset_row["instruction"]
        response_text = dataset_row["response"]

        if "python" in instruction_text.lower() and len(instruction_text) + len(response_text) < 5000:
            prompt_messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": instruction_text},
                {"role": "assistant", "content": response_text},
            ]
            python_examples.append({"messages": prompt_messages})

    print(f"✅ Prepared {len(python_examples)} Python training items.")

    # 4. Shuffle and Split
    random.seed(42)
    random.shuffle(python_examples)

    split_index = int(len(python_examples) * 0.95)
    train_data = python_examples[:split_index]
    validation_data = python_examples[split_index:]

    # 5. Save to JSONL
    print(f"💾 Saving {len(train_data)} training and {len(validation_data)} validation samples...")

    train_file_path = data_directory / "train.jsonl"
    with open(train_file_path, "w", encoding="utf-8") as train_file:
        for item_data in train_data:
            train_file.write(json.dumps(item_data) + "\n")

    validation_file_path = data_directory / "valid.jsonl"
    with open(validation_file_path, "w", encoding="utf-8") as validation_file:
        for item_data in validation_data:
            validation_file.write(json.dumps(item_data) + "\n")

    print(f"✨ Preparation complete! Data is ready in: {data_directory.absolute()}")


if __name__ == "__main__":
    prepare_data()
