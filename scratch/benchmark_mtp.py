import asyncio
import json
import time

import httpx


async def run_benchmark(prompt_text: str, model_id: str) -> None:
    api_url = "http://127.0.0.1:8000/v1/chat/completions"
    request_headers = {"Authorization": "Bearer vUYmhvvVwRSwW58", "Content-Type": "application/json"}
    request_payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt_text}],
        "stream": True,
        "temperature": 0.0,  # Use temperature 0.0 for deterministic generation
    }

    print(f"Connecting to {api_url} for model '{model_id}'...")

    start_request_time = time.perf_counter()
    first_token_time = None
    last_token_time = None
    token_count = 0
    full_response_text = ""

    try:
        async with httpx.AsyncClient(timeout=120.0) as http_client:
            async with http_client.stream(
                "POST", api_url, headers=request_headers, json=request_payload
            ) as http_response:
                if http_response.status_code != 200:
                    print(f"Error status: {http_response.status_code}")
                    error_bytes = await http_response.aread()
                    print(f"Error body: {error_bytes.decode('utf-8', errors='replace')}")
                    return

                async for line in http_response.aiter_lines():
                    if line.startswith("data: "):
                        data_string = line[6:]
                        if data_string == "[DONE]":
                            break

                        try:
                            chunk_data = json.loads(data_string)
                            choices = chunk_data.get("choices", [])
                            if not choices:
                                # Sometimes usage info comes in the last chunk without choices
                                continue

                            delta = choices[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                if token_count == 0:
                                    first_token_time = time.perf_counter()
                                token_count += 1
                                full_response_text += content
                                # Print chunk to show progress
                                print(content, end="", flush=True)
                        except json.JSONDecodeError:
                            print(f"\nJSON Decode Error on line: {line}")

                last_token_time = time.perf_counter()
    except Exception as exception:
        print(f"\nConnection or processing error: {exception}")
        return

    print("\n" + "=" * 40)
    print("Benchmark Results:")
    print("=" * 40)
    if first_token_time is not None and last_token_time is not None:
        prefill_duration = first_token_time - start_request_time
        generation_duration = last_token_time - first_token_time
        total_duration = last_token_time - start_request_time

        print(f"Prefill duration (Time to first token): {prefill_duration:.4f} seconds")
        print(f"Generation duration: {generation_duration:.4f} seconds")
        print(f"Total duration: {total_duration:.4f} seconds")
        print(f"Total tokens generated: {token_count}")
        if generation_duration > 0:
            tokens_per_second = token_count / generation_duration
            print(f"Token generation speed: {tokens_per_second:.2f} tokens/second")
        else:
            print("Generation duration was zero, cannot calculate speed.")
    else:
        print("No tokens were successfully generated.")
    print("=" * 40)


if __name__ == "__main__":
    benchmark_prompt = (
        "Write a comprehensive essay describing the importance of photosynthesis in the Earth's ecosystem."
    )
    target_model = "Qwen3.6-27B-oQ4-fp16-mtp"

    # Run twice: once for warm-up (and loading the model if needed), and once for the actual benchmark.
    print("--- Starting Warm-up Run ---")
    asyncio.run(run_benchmark("Write a brief sentence.", target_model))

    print("\n--- Starting Actual Benchmark Run ---")
    asyncio.run(run_benchmark(benchmark_prompt, target_model))
