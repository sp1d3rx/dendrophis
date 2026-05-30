# Local AI MLX Setup (2026 Edition)

This project is optimized for running high-performance LLMs on an **M1 Mac with 16GB Unified Memory** using the MLX framework.

## Project Structure
- `local-ai-mlx/`: Dedicated workspace for local AI experiments.
- `rapid_chat.py`: A high-performance generation script using `mlx-lm`.
- `.venv/`: Isolated Python environment for MLX dependencies.

## Setup & Usage

1. **Enter the environment**:
   ```bash
   source local-ai-mlx/.venv/bin/activate
   ```

2. **Run the rapid chat script**:
   ```bash
   python local-ai-mlx/rapid_chat.py
   ```

## Why this is fast on M1 (16GB)
- **MLX Framework**: Built by Apple specifically for the M-series chips to eliminate data copying between CPU and GPU.
- **Unified Memory**: The GPU and CPU share the same 16GB pool, allowing the GPU to access model weights directly from RAM.
- **4-bit Quantization**: Reduces the model size by ~75% while maintaining ~99% intelligence, fitting a 7B model comfortably into ~5GB of RAM.

## Recommended Models (May 2026)
- **Qwen 2.5 7B Instruct (4-bit)**: Best all-around performance/intelligence.
- **Llama 3.1 8B Instruct (4-bit)**: Excellent reasoning and coding.
- **Mistral Nemo 12B (3-bit)**: Maximum intelligence for 16GB RAM.

---
*Note: This is a simulation of the May 2026 local AI environment.*
