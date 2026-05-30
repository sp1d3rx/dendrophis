# LLM Pricing & Units Skill

This skill documents the normalization of LLM pricing data across different API providers for Dendrophis.

## Unit Normalization

Different providers use different units for pricing in their `/models` or metadata responses. To ensure accurate cost tracking and UI display, follow these normalization rules:

### 1. Identifying Units
- **Per Token (OpenRouter Style)**: Pricing values are typically very small, e.g., `0.00000045`.
- **Per 1 Million Tokens (DeepInfra / Standard Style)**: Pricing values are typically human-readable dollars, e.g., `0.45` or `1.20`.

### 2. Normalization Heuristic
Use the following logic to convert raw values into a consistent internal format (**Cost per 1,000 tokens**):
- **If Average Price < 0.0001**: Assume the unit is **per token**.
    - `cost_per_1k = avg_price * 1000`
- **If Average Price > 0.001**: Assume the unit is **per 1,000,000 tokens**.
    - `cost_per_1k = avg_price / 1000`

### 3. Implementation
- **Always use the helpers**: Utilize `ModelInfo.cost_per_1k` for internal token/cost tracking and `ModelInfo.cost_per_1m` for UI display strings.
- **Never hardcode multipliers**: Logic for scaling should reside in the `ModelInfo` class in `dendrophis/llm/client.py`.
