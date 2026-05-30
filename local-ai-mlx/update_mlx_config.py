import os
import sys

import yaml


def update_config(model_path, context_limit):
    config_path = "mlx.yaml"
    if not os.path.exists(config_path):
        print(f"Error: {config_path} not found")
        return

    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Update the relevant fields
    config["llm"]["model"] = model_path
    config["llm"]["context_limit"] = context_limit

    # Heuristic for max_tokens: larger context usually means we want more output room
    if context_limit >= 65536:
        config["llm"]["max_tokens"] = 16384
    else:
        config["llm"]["max_tokens"] = 8192

    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    print(f"✅ Updated {config_path} for model: {os.path.basename(model_path)}")
    print(f"📏 Context limit set to: {context_limit}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python update_mlx_config.py <model_path> <context_limit>")
    else:
        update_config(sys.argv[1], int(sys.argv[2]))
