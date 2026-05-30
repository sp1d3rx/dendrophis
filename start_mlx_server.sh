#!/bin/zsh

# Configuration
LM_STUDIO_MODELS="$HOME/.lmstudio/models"
PORT=8080

# 1. Find all MLX models (look for folders containing config.json)
echo "🔍 Scanning for MLX models in LM Studio..."
# Populate array using Zsh-friendly method
MODELS=( ${(f)"$(find "$LM_STUDIO_MODELS" -name "config.json" -exec dirname {} + | sort -u)"} )

if [ ${#MODELS} -eq 0 ]; then
    echo "❌ No MLX models found in $LM_STUDIO_MODELS"
    exit 1
fi

# 2. Present interactive list
echo ""
echo "Select a model to launch:"
for i in {1..${#MODELS}}; do
    # Display a cleaner name (Publisher / ModelName)
    CLEAN_NAME=$(echo "${MODELS[$i]}" | sed "s|$LM_STUDIO_MODELS/||")
    echo "$i) $CLEAN_NAME"
done

echo ""
echo -n "Enter number (default 1): "
read CHOICE
CHOICE=${CHOICE:-1}

SELECTED_MODEL="${MODELS[$CHOICE]}"

if [ -z "$SELECTED_MODEL" ]; then
    echo "❌ Invalid selection."
    exit 1
fi

# 3. Determine optimized parameters for 16GB M1
MODEL_NAME_LOW=$(echo "$SELECTED_MODEL" | tr '[:upper:]' '[:lower:]')
CONTEXT=32768 # Default

if [[ "$MODEL_NAME_LOW" == *"1b"* ]] || [[ "$MODEL_NAME_LOW" == *"3b"* ]] || [[ "$MODEL_NAME_LOW" == *"4b"* ]]; then
    # Small models can handle massive context on 16GB
    CONTEXT=65536
elif [[ "$MODEL_NAME_LOW" == *"30b"* ]] || [[ "$MODEL_NAME_LOW" == *"70b"* ]]; then
    # Large models need to be very conservative
    CONTEXT=8192
fi

# 4. Update mlx.yaml using our python helper
source local-ai-mlx/.venv/bin/activate
python local-ai-mlx/update_mlx_config.py "$SELECTED_MODEL" "$CONTEXT"

# 5. Launch the server
echo ""
echo "🚀 Launching MLX Server..."
echo "📦 Path: $SELECTED_MODEL"
echo "📏 Context: $CONTEXT"
echo ""

python -m mlx_lm server \
    --model "$SELECTED_MODEL" \
    --port "$PORT" \
    --prompt-cache-size 10 \
    --max-tokens 8192 \
    --use-default-chat-template
