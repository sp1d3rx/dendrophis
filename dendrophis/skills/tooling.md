# Tooling Integrity Skill

This skill provides critical instructions for ensuring robust and safe tool calling within the Dendrophis project.

## 1. Avoid Over-Escaping
- When using `write` or `edit` tools, provide the **RAW** string content exactly as it should appear in the file.
- **NEVER** manually escape double quotes (e.g., writing `\"` instead of `"`) or triple quotes inside the tool call. The JSON RPC layer handles the necessary escaping for the transport; adding your own will corrupt the target file.
- **NEVER** use escaped newline characters like `\n` in tool call arguments meant for file content; use literal newlines.

## 2. Contextual Accuracy
- For `edit` calls, always include at least 2-3 lines of surrounding context in `old_string` to ensure the match is 100% unique.
- Before performing any edit, use `read` or `ripgrep` to confirm the exact indentation, line endings, and whitespace of the target block.

## 3. Path Safety
- Always use absolute paths for `file_path` parameters.
- Verify that a file exists and is readable before attempting to edit or overwrite it.
