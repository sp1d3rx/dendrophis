"""Visual prompt renderer — encodes text/prompts into 8-bit Grayscale PNG Data URIs for VLM models."""

from __future__ import annotations

import base64
import hashlib
import json
from io import BytesIO
from pathlib import Path

SCRATCH_DIR = Path("/Users/derekw/Documents/projects/boiga/scratch")

# Global in-memory cache to prevent re-rendering identical prompt images across chat turns
_VISUAL_RENDER_CACHE: dict[str, str] = {}


def is_vlm_model(model_name: str) -> bool:
    """Return True if the target model is detected or calibrated as a VLM / vision model."""
    if not model_name:
        return False

    model_lower = model_name.lower()

    # 1. Heuristic matching on model ID
    vlm_keywords = [
        "gemma-4",
        "claude-3-5",
        "claude-3",
        "gpt-4o",
        "gemini-1.5",
        "gemini-2.0",
        "qwen2.5-vl",
        "llava",
        "vision",
        "vlm",
        "pixtral",
    ]
    if any(keyword in model_lower for keyword in vlm_keywords):
        return True

    # 2. Check calibration store override
    try:
        from dendrophis.llm.calibration import ModelOverrideStore

        store = ModelOverrideStore()
        capabilities = store.get(model_name)
        if capabilities and capabilities.supports_vlm:
            return True
    except Exception:
        pass

    return False


def format_tool_output_for_visual_rendering(text_content: str) -> str:
    """Format raw JSON tool outputs (e.g. glob, list_dir, search) into clean, human-readable line-numbered text."""
    trimmed_text = text_content.strip()
    if not trimmed_text.startswith("{") or not trimmed_text.endswith("}"):
        return text_content

    try:
        parsed_json = json.loads(trimmed_text)
        if not isinstance(parsed_json, dict):
            return text_content

        # Case 1: glob output {"files": [...], "count": N}
        if "files" in parsed_json and isinstance(parsed_json["files"], list):
            files_list = parsed_json["files"]
            formatted_lines = [f"=== GLOB SEARCH RESULTS ({len(files_list)} files) ==="]
            for index_num, file_path in enumerate(files_list, 1):
                formatted_lines.append(f"{index_num:2d}. {file_path}")
            return "\n".join(formatted_lines)

        # Case 2: list_dir output {"path": "...", "entries": [...]}
        if "path" in parsed_json and "entries" in parsed_json:
            directory_path = parsed_json.get("path", "")
            entries_list = parsed_json.get("entries", [])
            formatted_lines = [f"=== DIRECTORY CONTENTS: {directory_path} ({len(entries_list)} items) ==="]
            for index_num, item_name in enumerate(entries_list, 1):
                formatted_lines.append(f"{index_num:2d}. {item_name}")
            return "\n".join(formatted_lines)

        # Case 3: search_memory or ripgrep results {"results": [...]}
        if "results" in parsed_json and isinstance(parsed_json["results"], list):
            results_list = parsed_json["results"]
            formatted_lines = [f"=== SEARCH RESULTS ({len(results_list)} items) ==="]
            for index_num, result_item in enumerate(results_list, 1):
                if isinstance(result_item, dict):
                    summary_text = result_item.get("summary") or result_item.get("snippet") or str(result_item)
                    tags_list = result_item.get("tags", [])
                    tag_str = f" [{', '.join(tags_list)}]" if tags_list else ""
                    formatted_lines.append(f"[{index_num}] {summary_text[:120]}{tag_str}")
                else:
                    formatted_lines.append(f"[{index_num}] {result_item}")
            return "\n".join(formatted_lines)

    except Exception:
        pass

    return text_content


def render_text_to_1bit_data_uri(
    text_content: str,
    font_name: str = "Menlo.ttc",
    font_size: int = 16,
    padding: int = 20,
    max_target_width: int = 900,
    save_prefix: str = "visual_render",
) -> str | None:
    """Render text_content into an 8-bit Grayscale PNG image and return as a Base64 Data URI.

    Uses deterministic SHA256 content hashing to cache results in memory and disk,
    preventing duplicate file creation or re-rendering on subsequent turns.
    """
    if not text_content.strip():
        return None

    text_to_render = format_tool_output_for_visual_rendering(text_content)

    # Compute deterministic content hash key for caching
    cache_signature = f"{font_name}:{font_size}:{padding}:{max_target_width}:{text_to_render}"
    hash_key_digest = hashlib.sha256(cache_signature.encode("utf-8")).hexdigest()[:16]

    # Return cached Data URI if rendered previously in this process
    if hash_key_digest in _VISUAL_RENDER_CACHE:
        return _VISUAL_RENDER_CACHE[hash_key_digest]

    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None

    try:
        try:
            font_object = ImageFont.truetype(font_name, font_size)
        except Exception:
            try:
                font_object = ImageFont.truetype("Helvetica.ttc", font_size)
            except Exception:
                font_object = ImageFont.load_default()

        dummy_image = Image.new("L", (1, 1))
        dummy_draw = ImageDraw.Draw(dummy_image)

        lines = text_to_render.splitlines()
        wrapped_lines: list[str] = []

        for line in lines:
            if not line:
                wrapped_lines.append("")
                continue
            words = line.split(" ")
            current_line_words: list[str] = []

            for word in words:
                test_line = " ".join([*current_line_words, word])
                text_box = dummy_draw.textbbox((0, 0), test_line, font=font_object)
                line_width = text_box[2] - text_box[0]

                if line_width > max_target_width and current_line_words:
                    wrapped_lines.append(" ".join(current_line_words))
                    current_line_words = [word]
                else:
                    current_line_words.append(word)

            if current_line_words:
                wrapped_lines.append(" ".join(current_line_words))

        max_measured_width = 0
        for wrapped_line in wrapped_lines:
            if wrapped_line:
                bbox = dummy_draw.textbbox((0, 0), wrapped_line, font=font_object)
                width = bbox[2] - bbox[0]
                if width > max_measured_width:
                    max_measured_width = width

        single_line_box = dummy_draw.textbbox((0, 0), "Ag", font=font_object)
        line_height = max(font_size + 4, int((single_line_box[3] - single_line_box[1]) * 1.35))

        image_width = max_measured_width + padding * 2
        image_height = len(wrapped_lines) * line_height + padding * 2

        # 8-bit Grayscale Canvas: 0 = Black background, 255 = White text with FreeType anti-aliasing
        canvas_image = Image.new("L", (image_width, image_height), color=0)
        draw_context = ImageDraw.Draw(canvas_image)

        current_y = padding
        for line_text in wrapped_lines:
            draw_context.text((padding, current_y), line_text, font=font_object, fill=255)
            current_y += line_height

        # Save debug copy using deterministic hash name to avoid duplicate file creation
        try:
            SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
            scratch_file_path = SCRATCH_DIR / f"{save_prefix}_{hash_key_digest}.png"
            if not scratch_file_path.exists():
                canvas_image.save(scratch_file_path, format="PNG")
        except Exception:
            pass

        image_buffer = BytesIO()
        canvas_image.save(image_buffer, format="PNG")
        raw_bytes = image_buffer.getvalue()
        encoded_base64 = base64.b64encode(raw_bytes).decode("utf-8")
        data_uri = f"data:image/png;base64,{encoded_base64}"

        # Store in global in-memory cache
        _VISUAL_RENDER_CACHE[hash_key_digest] = data_uri
        return data_uri

    except Exception:
        return None
