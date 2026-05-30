import os
import sys

import yaml


def update_config(model_path):
    config_path = "mlc.yaml"
    if not os.path.exists(config_path):
        # Copy from mlx.yaml if it doesn't exist
        if os.path.exists("mlx.yaml"):
            import shutil

            shutil.copy("mlx.yaml", "mlc.yaml")
        else:
            print("Error: No base config found")
            return

    with open(config_path) as f:
        config = yaml.safe_load(f)

    config["llm"]["model"] = model_path
    config["llm"]["base_url"] = "http://127.0.0.1:8081/v1"
    config["llm"]["context_limit"] = 32768

    # Ensure stable parameters are kept
    config["llm"]["min_p"] = 0.05
    config["llm"]["repetition_penalty"] = 1.2
    config["llm"]["frequency_penalty"] = 0.1
    config["llm"]["temperature"] = 0.1

    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    print(f"✅ Updated mlc.yaml for model: {os.path.basename(model_path)}")


if __name__ == "__main__":
    update_config(sys.argv[1])
