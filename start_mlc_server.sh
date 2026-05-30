#!/bin/zsh

# Configuration
MLC_MODELS_DIR="$HOME/.mlc-llm/models"
PORT=8081  # Use 8081 to avoid conflict with MLX on 8080

# 1. Find all MLC models
echo "🔍 Scanning for local MLC models in $MLC_MODELS_DIR..."
MODELS=( ${(f)"$(find "$MLC_MODELS_DIR" -name "mlc-chat-config.json" -exec dirname {} + | sort -u)"} )

# 2. Present interactive list
echo ""
echo "Select an MLC model to launch:"
for i in {1..${#MODELS}}; do
    CLEAN_NAME=$(basename "${MODELS[$i]}")
    echo "$i) $CLEAN_NAME"
done
echo "$(( ${#MODELS} + 1 ))) [Enter Hugging Face ID]"

echo ""
echo -n "Enter choice (default 1): "
read CHOICE
CHOICE=${CHOICE:-1}

if [ "$CHOICE" -gt "${#MODELS}" ]; then
    echo ""
    echo "Known good models for M1 (16GB):"
    echo "- HF://mlc-ai/Qwen3.5-9B-q4f16_1-MLC"
    echo "- HF://mlc-ai/Phi-4-mini-q4f16_1-MLC"
    echo ""
    echo -n "Enter HF ID (e.g. HF://mlc-ai/...): "
    read HF_ID
    SELECTED_MODEL="$HF_ID"
else
    SELECTED_MODEL="${MODELS[$CHOICE]}"
fi

if [ -z "$SELECTED_MODEL" ]; then
    echo "❌ Invalid selection."
    exit 1
fi

# 3. Update mlc.yaml
# We reuse the logic from the MLX update script but point to port 8081
# Note: For MLC, the context limit is often defined in mlc-chat-config.json
# We'll set a safe 32k for now.
source local-ai-mlx/.venv/bin/activate
# We'll create a slightly modified version of the update script for MLC
cat > local-ai-mlc/update_mlc_config.py <<EOF
import yaml
import sys
import os

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

    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    config['llm']['model'] = model_path
    config['llm']['base_url'] = "http://127.0.0.1:8081/v1"
    config['llm']['context_limit'] = 32768
    
    # Ensure stable parameters are kept
    config['llm']['min_p'] = 0.05
    config['llm']['repetition_penalty'] = 1.2
    config['llm']['frequency_penalty'] = 0.1
    config['llm']['temperature'] = 0.1
    
    with open(config_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    
    print(f"✅ Updated mlc.yaml for model: {os.path.basename(model_path)}")

if __name__ == "__main__":
    update_config(sys.argv[1])
EOF

python local-ai-mlc/update_mlc_config.py "$SELECTED_MODEL"

# 4. Launch the server
source local-ai-mlc/.venv/bin/activate
echo ""
echo "🚀 Launching MLC LLM Server..."
echo "📦 Model: $(basename "$SELECTED_MODEL")"
echo "🌐 Port: $PORT"
echo ""

python -m mlc_llm serve "$SELECTED_MODEL" --port "$PORT" --mode interactive --prefix-cache-mode disable --overrides "context_window_size=16384"
