import tiktoken

from dendrophis.context.tokenizer import _heuristic_estimate


def verify_accuracy():
    enc = tiktoken.get_encoding("cl100k_base")
    test_cases = [
        "Hello, world!",
        "The quick brown fox jumps over the lazy dog.",
        "Python is a great programming language.",
        "    Indented code snippet\n    with multiple lines.",
        "Unicode test: 😊 🚀 🌍",
        "A very long sentence " * 10,
        "Complex punctuation! (Maybe?); Yes: [Indeed].",
    ]

    print(f"{'Text Snippet':<40} | {'Tiktoken':<8} | {'Heuristic':<10} | {'Diff'}")
    print("-" * 75)
    for text in test_cases:
        actual = len(enc.encode(text))
        heuristic = _heuristic_estimate(text)
        diff = heuristic - actual
        snippet = text[:37] + "..." if len(text) > 37 else text
        print(f"{snippet:<40} | {actual:<8} | {heuristic:<10} | {diff:+d}")


if __name__ == "__main__":
    verify_accuracy()
