import json
import random
from pathlib import Path


def generate_synthetic_data():
    print("🧬 Synthesizing high-quality Raymond Hettinger coding examples...")

    # Naming lists and singulars for combinatorial generation
    list_vocab = [
        ("names", "name"),
        ("prices", "price"),
        ("users", "user"),
        ("products", "product"),
        ("scores", "score"),
        ("temperatures", "temperature"),
        ("cities", "city"),
        ("books", "book"),
        ("errors", "error"),
        ("messages", "message"),
        ("colors", "color"),
        ("animals", "animal"),
        ("words", "word"),
        ("lines", "line"),
        ("students", "student"),
        ("employees", "employee"),
        ("dates", "date"),
        ("records", "record"),
        ("queries", "query"),
        ("files", "file"),
    ]

    dict_vocab = [
        ("user_mapping", "user_id"),
        ("price_catalog", "product_name"),
        ("email_registry", "username"),
        ("product_inventory", "barcode"),
        ("score_board", "player_name"),
        ("city_coordinates", "city_name"),
        ("book_authors", "isbn"),
        ("error_codes", "status_code"),
        ("metadata", "attribute_key"),
        ("config", "parameter_name"),
    ]

    default_values = ["'unknown'", "0", "0.0", "None", "'default'", "'N/A'"]

    synthetic_examples = []
    system_prompt = "You are a senior Python core developer who follows Raymond Hettinger's advice. Refactor the code to be more idiomatic and efficient."  # noqa: E501

    # Pattern 1: Loop over index -> enumerate
    for list_name, item_name in list_vocab:
        instruction_1 = f"Refactor this loop to be more pythonic:\n\n```python\nfor index in range(len({list_name})):\n    print(index, {list_name}[index])\n```"  # noqa: E501
        response_1 = f"Use `enumerate()` to iterate over the index and elements of the list simultaneously without manual indexing:\n\n```python\nfor index, {item_name} in enumerate({list_name}):\n    print(index, {item_name})\n```"  # noqa: E501

        instruction_2 = f"How do I refactor this range-len index loop in Python?\n\n```python\nfor index in range(len({list_name})):\n    item = {list_name}[index]\n    process_item(item)\n```"  # noqa: E501
        response_2 = f"If you do not need the index, iterate over the collection directly. This is cleaner and more pythonic:\n\n```python\nfor {item_name} in {list_name}:\n    process_item({item_name})\n```"  # noqa: E501

        synthetic_examples.append(
            {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": instruction_1},
                    {"role": "assistant", "content": response_1},
                ]
            }
        )
        synthetic_examples.append(
            {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": instruction_2},
                    {"role": "assistant", "content": response_2},
                ]
            }
        )

    # Pattern 2: Parallel loop -> zip
    for index_a, (list_a, item_a) in enumerate(list_vocab):
        index_b = (index_a + 1) % len(list_vocab)
        list_b, item_b = list_vocab[index_b]

        instruction = f"Refactor this parallel loop to be more pythonic:\n\n```python\nfor index in range(len({list_a})):\n    print({list_a}[index], '-->', {list_b}[index])\n```"  # noqa: E501
        response = f"Use `zip()` to iterate over multiple sequences in parallel. It is cleaner, faster, and avoids manual index tracking:\n\n```python\nfor {item_a}, {item_b} in zip({list_a}, {list_b}):\n    print({item_a}, '-->', {item_b})\n```"  # noqa: E501

        synthetic_examples.append(
            {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": instruction},
                    {"role": "assistant", "content": response},
                ]
            }
        )

    # Pattern 3: Dict check and get -> dict.get()
    for dict_name, key_name in dict_vocab:
        for default_val in default_values:
            instruction = f"How should I refactor this manual key checking code?\n\n```python\nif {key_name} in {dict_name}.keys():\n    value = {dict_name}[{key_name}]\nelse:\n    value = {default_val}\n```"  # noqa: E501
            response = f"Do not call `.keys()` on a dictionary for membership testing, as dictionary lookup is already highly optimized. Additionally, use `.get()` to retrieve the value with a default in a single, atomic operation:\n\n```python\nvalue = {dict_name}.get({key_name}, {default_val})\n```"  # noqa: E501

            synthetic_examples.append(
                {
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": instruction},
                        {"role": "assistant", "content": response},
                    ]
                }
            )

    # Pattern 4: Dict grouping -> defaultdict
    for _, key_name in dict_vocab:
        value_name = "item" if key_name != "item_id" else "record"
        instruction = f"Refactor this grouping loop to be more efficient:\n\n```python\ngrouped_data = {{}}\nfor {key_name}, {value_name} in data:\n    if {key_name} not in grouped_data:\n        grouped_data[{key_name}] = []\n    grouped_data[{key_name}].append({value_name})\n```"  # noqa: E501
        response = f"Use `collections.defaultdict` to automatically handle key initialization. This makes the loop cleaner, more expressive, and faster:\n\n```python\nfrom collections import defaultdict\n\ngrouped_data = defaultdict(list)\nfor {key_name}, {value_name} in data:\n    grouped_data[{key_name}].append({value_name})\n```"  # noqa: E501

        synthetic_examples.append(
            {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": instruction},
                    {"role": "assistant", "content": response},
                ]
            }
        )

    # Pattern 5: String concat -> join
    for list_name, item_name in list_vocab:
        for separator in ["' '", "', '", "'|'"]:
            instruction = f"Refactor this string concatenation loop:\n\n```python\ncombined_string = ''\nfor {item_name} in {list_name}:\n    combined_string += {item_name} + {separator}\n```"  # noqa: E501
            response = f"Do not use `+` or `+=` string concatenation inside a loop. Strings are immutable in Python, so this creates a new string copy on every iteration, leading to O(N^2) complexity. Use `str.join()` for clean and optimal O(N) performance:\n\n```python\ncombined_string = {separator}.join({list_name})\n```"  # noqa: E501

            synthetic_examples.append(
                {
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": instruction},
                        {"role": "assistant", "content": response},
                    ]
                }
            )

    # Pattern 6: Manual count -> Counter
    for list_name, item_name in list_vocab:
        instruction = f"Refactor this counting loop to be more pythonic:\n\n```python\ncounts = {{}}\nfor {item_name} in {list_name}:\n    if {item_name} not in counts:\n        counts[{item_name}] = 0\n    counts[{item_name}] += 1\n```"  # noqa: E501
        response = f"Use `collections.Counter` to perform counting efficiently in a single step:\n\n```python\nfrom collections import Counter\n\ncounts = Counter({list_name})\n```"  # noqa: E501

        synthetic_examples.append(
            {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": instruction},
                    {"role": "assistant", "content": response},
                ]
            }
        )

    # Pattern 7: Unnecessary Lambda in Sort -> itemgetter / attrgetter
    for _, key_name in dict_vocab:
        instruction_1 = f"Refactor this sort key:\n\n```python\nsorted_data = sorted(data, key=lambda item: item['{key_name}'])\n```"  # noqa: E501
        response_1 = f"Avoid using lambda functions for simple item extraction. Use `operator.itemgetter` for cleaner, faster lookups:\n\n```python\nfrom operator import itemgetter\n\nsorted_data = sorted(data, key=itemgetter('{key_name}'))\n```"  # noqa: E501

        instruction_2 = (
            f"Refactor this sort key:\n\n```python\nsorted_data = sorted(data, key=lambda item: item.{key_name})\n```"
        )
        response_2 = f"Avoid using lambda functions for simple attribute extraction. Use `operator.attrgetter` for cleaner, faster lookups:\n\n```python\nfrom operator import attrgetter\n\nsorted_data = sorted(data, key=attrgetter('{key_name}'))\n```"  # noqa: E501

        synthetic_examples.append(
            {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": instruction_1},
                    {"role": "assistant", "content": response_1},
                ]
            }
        )
        synthetic_examples.append(
            {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": instruction_2},
                    {"role": "assistant", "content": response_2},
                ]
            }
        )

    # Pattern 8: Iterating over slices -> islice
    for list_name, item_name in list_vocab:
        instruction = f"Refactor this loop that only iterates over the first 100 elements:\n\n```python\nfor index in range(min(100, len({list_name}))):\n    item = {list_name}[index]\n    process(item)\n```"  # noqa: E501
        response = f"Use `itertools.islice()` to slice an iterable efficiently without creating a full list copy in memory:\n\n```python\nfrom itertools import islice\n\nfor {item_name} in islice({list_name}, 100):\n    process({item_name})\n```"  # noqa: E501

        synthetic_examples.append(
            {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": instruction},
                    {"role": "assistant", "content": response},
                ]
            }
        )

    # Pattern 9: Manual search loop -> any / all
    for list_name, item_name in list_vocab:
        instruction_1 = f"Refactor this manual check loop to be pythonic:\n\n```python\nhas_invalid = False\nfor {item_name} in {list_name}:\n    if is_invalid({item_name}):\n        has_invalid = True\n        break\n```"  # noqa: E501
        response_1 = f"Use the built-in `any()` function combined with a generator expression. This halts evaluation as soon as a match is found:\n\n```python\nhas_invalid = any(is_invalid({item_name}) for {item_name} in {list_name})\n```"  # noqa: E501

        instruction_2 = f"Refactor this manual check loop to be pythonic:\n\n```python\nall_valid = True\nfor {item_name} in {list_name}:\n    if not is_valid({item_name}):\n        all_valid = False\n        break\n```"  # noqa: E501
        response_2 = f"Use the built-in `all()` function combined with a generator expression. This halts evaluation as soon as a mismatch is found:\n\n```python\nall_valid = all(is_valid({item_name}) for {item_name} in {list_name})\n```"  # noqa: E501

        synthetic_examples.append(
            {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": instruction_1},
                    {"role": "assistant", "content": response_1},
                ]
            }
        )
        synthetic_examples.append(
            {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": instruction_2},
                    {"role": "assistant", "content": response_2},
                ]
            }
        )

    # Pattern 10: Manual Dictionary Merging -> ChainMap
    for dict_name, _ in dict_vocab:
        instruction = f"Refactor this dictionary merging code:\n\n```python\nmerged_dictionary = {{}}\nmerged_dictionary.update({dict_name}_first)\nmerged_dictionary.update({dict_name}_second)\n```"  # noqa: E501
        response = f"Use `collections.ChainMap` to group multiple dictionaries together to create a single, updateable view without copying mapping data:\n\n```python\nfrom collections import ChainMap\n\nmerged_dictionary = ChainMap({dict_name}_first, {dict_name}_second)\n```"  # noqa: E501

        synthetic_examples.append(
            {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": instruction},
                    {"role": "assistant", "content": response},
                ]
            }
        )

    # Load existing datasets and merge
    data_directory = Path("scratch/lora_training/data_lfm")
    train_file_path = data_directory / "train.jsonl"

    if not train_file_path.exists():
        print("⚠️ Existing train.jsonl not found. Preparing new files.")
        return

    print("📖 Loading existing train.jsonl data...")
    with open(train_file_path, encoding="utf-8") as train_file:
        existing_train_data = [json.loads(line_content) for line_content in train_file if line_content.strip()]

    # Weight the synthetic data to ensure it represents a good ratio
    weighted_synthetic = []
    for example_item in synthetic_examples:
        weighted_synthetic.extend([example_item] * 10)

    combined_train_data = existing_train_data + weighted_synthetic
    random.shuffle(combined_train_data)

    print(
        f"💾 Saving {len(combined_train_data)} combined training examples "
        f"(including {len(weighted_synthetic)} weighted synthetic items)..."
    )
    with open(train_file_path, "w", encoding="utf-8") as train_file:
        for item_data in combined_train_data:
            train_file.write(json.dumps(item_data) + "\n")

    print("✨ Synthesis and dataset merge completed successfully!")


if __name__ == "__main__":
    generate_synthetic_data()
