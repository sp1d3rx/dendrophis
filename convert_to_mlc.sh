#!/bin/zsh

# Configuration
LOCAL_MLC_DIR="./local-ai-mlc"
MLC_MODELS_DIR="$HOME/.mlc-llm/models"
mkdir -p "$MLC_MODELS_DIR"

if [ -z "$1" ]; then
    echo "Usage: ./convert_to_mlc.sh <path_to_mlx_model_folder_OR_huggingface_id>"
    exit 1
fi

INPUT_SOURCE="$1"

# Determine if it's a local path or HF ID
if [ -d "$INPUT_SOURCE" ]; then
    SOURCE_PATH="$INPUT_SOURCE"
    MODEL_NAME=$(basename "$SOURCE_PATH")
    echo "📂 Using local source: $SOURCE_PATH"
else
    SOURCE_PATH="$INPUT_SOURCE"
    MODEL_NAME=$(echo "$SOURCE_PATH" | tr '/' '-')
    echo "🌐 Using Hugging Face ID: $SOURCE_PATH"
fi

OUTPUT_PATH="$MLC_MODELS_DIR/$MODEL_NAME-MLC"

echo "🔄 Converting MLX model to MLC format..."
echo "📦 Source: $SOURCE_PATH"
echo "🎯 Output: $OUTPUT_PATH"

source "$LOCAL_MLC_DIR/.venv/bin/activate"

# 1. Detect Conversation Template
MODEL_NAME_LOW=$(echo "$MODEL_NAME" | tr '[:upper:]' '[:lower:]')
TEMPLATE="chatml" # Default fallback

if [[ "$MODEL_NAME_LOW" == *"qwen3.5"* ]] || [[ "$MODEL_NAME_LOW" == *"qwen3-5"* ]]; then
    TEMPLATE="qwen3_5"
elif [[ "$MODEL_NAME_LOW" == *"qwen3"* ]]; then
    TEMPLATE="qwen3"
elif [[ "$MODEL_NAME_LOW" == *"qwen2"* ]]; then
    TEMPLATE="qwen2"
elif [[ "$MODEL_NAME_LOW" == *"llama-3"* ]] || [[ "$MODEL_NAME_LOW" == *"llama3"* ]]; then
    TEMPLATE="llama-3"
elif [[ "$MODEL_NAME_LOW" == *"phi-4"* ]]; then
    TEMPLATE="phi-4"
elif [[ "$MODEL_NAME_LOW" == *"phi-3"* ]]; then
    TEMPLATE="phi-3"
elif [[ "$MODEL_NAME_LOW" == *"gemma"* ]]; then
    TEMPLATE="gemma_instruction"
fi

echo "🏷  Detected Template: $TEMPLATE"

# 1. Generate Config
echo "⚙ Generating MLC Config..."
python -m mlc_llm gen_config "$SOURCE_PATH" \
    --quantization q4f16_1 \
    --conv-template "$TEMPLATE" \
    --output "$OUTPUT_PATH"

# 2. Convert Weights
echo "⚖ Converting Weights..."
python -m mlc_llm convert_weight "$SOURCE_PATH" \
    --quantization q4f16_1 \
    --source "$SOURCE_PATH" \
    --output "$OUTPUT_PATH"

# 3. Compile Library (Metal for M1)
echo "🏗 Compiling Model Library (Metal)..."
python -m mlc_llm compile "$OUTPUT_PATH/mlc-chat-config.json" \
    --device metal \
    --output "$OUTPUT_PATH/$MODEL_NAME-metal.dylib"

echo ""
echo "✅ Conversion Complete!"
echo "📍 MLC Model ready at: $OUTPUT_PATH"
echo "🚀 You can now select this model in ./start_mlc_server.sh"
