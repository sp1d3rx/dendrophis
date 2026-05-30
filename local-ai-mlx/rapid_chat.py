import os
import time

from mlx_lm import generate, load


def main():
    print("🚀 Initializing Rapid-MLX Environment (2026 Simulation)...")

    # POINT TO YOUR LM STUDIO MODELS HERE
    # You can use the absolute path to any MLX model folder you already have.
    lm_studio_base = os.path.expanduser("~/.lmstudio/models")

    # Option 1: Qwen 3.5 9B (Higher intelligence, great for coding)
    model_path = os.path.join(lm_studio_base, "bigatuna/Qwen3.5-9b-Sushi-Coder-RL-MLX")

    # Option 2: Llama 3.2 1B (Extremely fast, low RAM)
    # model_path = os.path.join(lm_studio_base, "mlx-community/Llama-3.2-1B-Instruct-4bit")

    if not os.path.exists(model_path):
        print(f"⚠️ Local model not found at {model_path}")
        print("Falling back to Hugging Face download...")
        model_path = "mlx-community/Qwen2.5-7B-Instruct-4bit"
    else:
        print(f"✅ Found local LM Studio model: {os.path.basename(model_path)}")

    print(f"📦 Loading Model: {model_path}")
    model, tokenizer = load(model_path)

    # Define a sample prompt
    prompt = "Explain how unified memory works on Apple Silicon in simple terms."

    messages = [{"role": "user", "content": prompt}]
    formatted_prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

    print("\n--- Rapid-MLX Generation ---")
    start_time = time.time()

    # Generating with optimized parameters
    generate(model, tokenizer, prompt=formatted_prompt, verbose=True, temp=0.7, max_tokens=512)

    end_time = time.time()
    duration = end_time - start_time
    print("\n--- Metrics ---")
    print(f"Total time: {duration:.2f}s")
    # Note: tokens/sec will be printed by mlx-lm during generation if verbose=True


if __name__ == "__main__":
    main()
