import asyncio
import json
import time

import httpx


async def get_session_cookie(base_url: str, api_key: str) -> str:
    login_url = f"{base_url}/admin/auto-login?key={api_key}"
    async with httpx.AsyncClient() as client:
        response = await client.get(login_url)
        cookie_header = response.headers.get("set-cookie", "")
        if "omlx_admin_session=" in cookie_header:
            cookie_value = cookie_header.split("omlx_admin_session=")[1].split(";")[0]
            return cookie_value
        raise ValueError("Could not obtain session cookie from auto-login response.")

async def update_settings(base_url: str, model_id: str, cookie_value: str, settings_payload: dict) -> None:
    settings_url = f"{base_url}/admin/api/models/{model_id}/settings"
    headers = {
        "Cookie": f"omlx_admin_session={cookie_value}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient() as client:
        response = await client.put(settings_url, headers=headers, json=settings_payload)
        if response.status_code != 200:
            raise ValueError(f"Failed to update settings: {response.status_code} - {response.text}")

async def unload_model(base_url: str, model_id: str, cookie_value: str) -> None:
    unload_url = f"{base_url}/admin/api/models/{model_id}/unload"
    headers = {
        "Cookie": f"omlx_admin_session={cookie_value}"
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(unload_url, headers=headers)
        # 400 means already unloaded, which is fine
        if response.status_code not in (200, 400):
            raise ValueError(f"Failed to unload model: {response.status_code} - {response.text}")

async def run_query(base_url: str, model_id: str, prompt_text: str, is_warmup: bool = False) -> dict:
    completion_url = f"{base_url}/v1/chat/completions"
    headers = {
        "Authorization": "Bearer vUYmhvvVwRSwW58",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt_text}],
        "stream": True,
        "temperature": 0.0
    }

    start_request_time = time.perf_counter()
    first_token_time = None
    last_token_time = None
    token_count = 0

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            async with client.stream("POST", completion_url, headers=headers, json=payload) as response:
                if response.status_code != 200:
                    raise ValueError(f"Stream error: {response.status_code}")

                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_string = line[6:]
                        if data_string == "[DONE]":
                            break
                        
                        try:
                            chunk_data = json.loads(data_string)
                            choices = chunk_data.get("choices", [])
                            if not choices:
                                continue
                            
                            delta = choices[0].get("delta", {})
                            content = delta.get("content", "")
                            reasoning_content = delta.get("reasoning_content", "")
                            
                            # Thinking trace or standard content both count as generated tokens
                            if content or reasoning_content:
                                if token_count == 0:
                                    first_token_time = time.perf_counter()
                                token_count += 1
                                if not is_warmup:
                                    # Print progress dot or characters
                                    print(content or reasoning_content, end="", flush=True)
                        except json.JSONDecodeError:
                            pass
                
                last_token_time = time.perf_counter()
    except Exception as exception:
        print(f"\nQuery error: {exception}")
        return {"success": False, "error": str(exception)}

    prefill_duration = (first_token_time - start_request_time) if first_token_time else 0.0
    generation_duration = (last_token_time - first_token_time) if first_token_time else 0.0
    tokens_per_second = (token_count / generation_duration) if generation_duration > 0 else 0.0

    return {
        "success": True,
        "prefill_duration": prefill_duration,
        "generation_duration": generation_duration,
        "token_count": token_count,
        "tokens_per_second": tokens_per_second
    }

async def run_benchmark_for_config(base_url: str, model_id: str, cookie_value: str, config_name: str, settings_payload: dict) -> dict:
    print("\n" + "=" * 60)
    print(f"Configuring and testing: {config_name}")
    print("=" * 60)

    print("Updating settings...")
    await update_settings(base_url, model_id, cookie_value, settings_payload)

    print("Unloading model to apply settings...")
    await unload_model(base_url, model_id, cookie_value)

    # Allow a brief moment for the server to settle down
    await asyncio.sleep(2.0)

    print("Running warm-up (and loading model)...")
    warmup_result = await run_query(base_url, model_id, "Write a short sentence.", is_warmup=True)
    if not warmup_result["success"]:
        print(f"Warm-up failed: {warmup_result.get('error')}")
        return {"config": config_name, "success": False}
    print(f"\nWarm-up completed (Loaded model in {warmup_result['prefill_duration']:.2f}s, generated {warmup_result['token_count']} tokens)")

    # Sleep between runs to avoid overlap issues
    await asyncio.sleep(2.0)

    print("Running actual benchmark query...")
    benchmark_prompt = "Write a comprehensive essay describing the importance of photosynthesis in the Earth's ecosystem."
    benchmark_result = await run_query(base_url, model_id, benchmark_prompt, is_warmup=False)
    if not benchmark_result["success"]:
        print(f"Benchmark failed: {benchmark_result.get('error')}")
        return {"config": config_name, "success": False}

    print("\nBenchmark completed successfully:")
    print(f"  Prefill time: {benchmark_result['prefill_duration']:.4f}s")
    print(f"  Generation time: {benchmark_result['generation_duration']:.4f}s")
    print(f"  Tokens generated: {benchmark_result['token_count']}")
    print(f"  Generation speed: {benchmark_result['tokens_per_second']:.2f} tok/s")

    return {
        "config": config_name,
        "success": True,
        "prefill_duration": benchmark_result["prefill_duration"],
        "generation_duration": benchmark_result["generation_duration"],
        "token_count": benchmark_result["token_count"],
        "tokens_per_second": benchmark_result["tokens_per_second"]
    }

async def main():
    server_base_url = "http://127.0.0.1:8000"
    server_api_key = "vUYmhvvVwRSwW58"
    model_id = "Qwen3.6-27B-oQ4-fp16-mtp"

    print("Retrieving admin session cookie...")
    cookie_value = await get_session_cookie(server_base_url, server_api_key)

    configurations = [
        {
            "name": "Native MTP (Current)",
            "settings": {
                "mtp_enabled": True,
                "turboquant_kv_enabled": False,
                "dflash_enabled": False
            }
        },
        {
            "name": "Standard Baseline (MTP Disabled)",
            "settings": {
                "mtp_enabled": False,
                "turboquant_kv_enabled": False,
                "dflash_enabled": False
            }
        },
        {
            "name": "TurboQuant 4-bit KV Cache (MTP Disabled)",
            "settings": {
                "mtp_enabled": False,
                "turboquant_kv_enabled": True,
                "turboquant_kv_bits": 4.0,
                "dflash_enabled": False
            }
        },
        {
            "name": "TurboQuant 8-bit KV Cache (MTP Disabled)",
            "settings": {
                "mtp_enabled": False,
                "turboquant_kv_enabled": True,
                "turboquant_kv_bits": 8.0,
                "dflash_enabled": False
            }
        }
    ]

    benchmark_results = []

    try:
        for config_entry in configurations:
            result = await run_benchmark_for_config(
                server_base_url,
                model_id,
                cookie_value,
                config_entry["name"],
                config_entry["settings"]
            )
            benchmark_results.append(result)
    finally:
        # Restore original settings
        print("\nRestoring original settings (Native MTP enabled)...")
        original_settings = {
            "mtp_enabled": True,
            "turboquant_kv_enabled": False,
            "dflash_enabled": False
        }
        await update_settings(server_base_url, model_id, cookie_value, original_settings)
        await unload_model(server_base_url, model_id, cookie_value)
        print("Original settings restored and model unloaded.")

    # Print summary table
    print("\n\n" + "#" * 60)
    print("BENCHMARK SUITE SUMMARY")
    print("#" * 60)
    print("| Configuration | Prefill Time (s) | Generation Speed (tok/s) | Tokens Count |")
    print("|---|---|---|---|")
    for result in benchmark_results:
        if result["success"]:
            print(f"| {result['config']} | {result['prefill_duration']:.4f}s | {result['tokens_per_second']:.2f} tok/s | {result['token_count']} |")
        else:
            print(f"| {result['config']} | FAILED | FAILED | FAILED |")
    print("#" * 60 + "\n")

if __name__ == "__main__":
    asyncio.run(main())
