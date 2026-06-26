import argparse

from mlx_lm import generate, load
from mlx_lm.sample_utils import make_sampler

CHALLENGES = [
    {
        "name": "Manual Index Loop",
        "code": "for i in range(len(mylist)):\n    print(i, mylist[i])",
    },
    {
        "name": "List Append Loop",
        "code": "results = []\nfor item in data:\n    if item > 10:\n        results.append(item * 2)",
    },
    {
        "name": "Dictionary Membership",
        "code": "if key in my_dict.keys():\n    value = my_dict[key]\nelse:\n    value = 'default'",
    },
    {
        "name": "String Concatenation",
        "code": "s = ''\nfor word in words:\n    s += word + ' '",
    },
    {
        "name": "Unnecessary Lambda",
        "code": "sorted_data = sorted(data, key=lambda x: x.priority)",
    },
    {
        "name": "Manual Pairwise",
        "code": "for i in range(len(items) - 1):\n    current_item = items[i]\n    next_item = items[i+1]\n    print(current_item, next_item)",  # noqa: E501
    },
    {
        "name": "Itertools powerset",
        "code": "Write a powerset function using itertools that generates all possible subsets of an iterable.",
    },
    {
        "name": "Itertools unique_everseen",
        "code": "Write a unique_everseen function that yields unique elements preserving their original order.",
    },
]


def run_evaluation(model_path, adapter_path=None):
    print(f"📦 Loading model: {model_path}")
    if adapter_path:
        print(f"🧩 Applying LoRA adapter from: {adapter_path}")
        model, tokenizer = load(model_path, adapter_path=adapter_path)
    else:
        model, tokenizer = load(model_path)

    system_prompt = "You are a senior Python core developer who follows Raymond Hettinger's advice. Refactor the code to be more idiomatic and efficient."  # noqa: E501

    for challenge_item in CHALLENGES:
        print("\n" + "=" * 60)
        print(f"Challenge: {challenge_item['name']}")
        print("-" * 60)
        print(f"Input Code:\n{challenge_item['code']}")
        print("-" * 60)

        prompt_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Refactor this:\n\n{challenge_item['code']}"},
        ]
        prompt_text = tokenizer.apply_chat_template(prompt_messages, add_generation_prompt=True)

        try:
            print("Assistant Output:")
            generate(
                model,
                tokenizer,
                prompt=prompt_text,
                verbose=True,
                sampler=make_sampler(temp=0.1),
                max_tokens=300,
            )
        except Exception as exception_error:
            print(f"⚠️ Error generating response: {exception_error}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate Raymond Hettinger pythonic model")
    parser.add_argument(
        "--model",
        type=str,
        default="mlx-community/LFM2.5-8B-A1B-MLX-8bit",
        help="Path or HuggingFace repo ID of the model",
    )
    parser.add_argument(
        "--adapter",
        type=str,
        default="scratch/lora_training/lfm_adapters",
        help="Path to the LoRA adapters directory",
    )
    parser.add_argument(
        "--base-only",
        action="store_true",
        help="Evaluate base model without adapters",
    )

    args = parser.parse_args()

    active_adapter = None if args.base_only else args.adapter
    run_evaluation(model_path=args.model, adapter_path=active_adapter)
