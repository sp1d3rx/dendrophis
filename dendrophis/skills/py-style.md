---
name: py-style
aliases: [py-codestyle, python-style]
description: >
  Python style guide based on PEP 8 and Raymond Hettinger's principles for beautiful, pythonic code.
  Enforces meaningful snake_case naming, prohibits single-letter variables, and promotes idiomatic
  Python patterns like EAFP, comprehensions, context managers, and proper unpacking.
  Provides auto-fixes for safe cases and outputs suggestions in diff/patch format.
---

# Python Style Guide: PEP 8 & Raymond Hettinger's Principles

This skill analyzes Python code and provides suggestions based on PEP 8 and Raymond Hettinger's
advice for writing beautiful, pythonic code. It flags unpythonic patterns and suggests
improvements with clear explanations.

**Python Version:** 3.13+
**Severity:** Style and naming rules (strictly no single-letter variable names)
**Output Format:** Diff/patch format with balanced verbosity (issue + why it matters)

---

## Core Rules

### 1. No Single-Letter Variables (Strict)

**Why it matters:** Single-letter names fail to communicate intent. Code should read like
prose, and every variable name should tell you what it represents without studying the
surrounding code. Only `_` as a throwaway unpacking wildcard is permitted.

**Bad:**
```python
def process(d, k):
    return d[k]
```

**Good:**
```python
def get_user_by_id(users_by_id: dict[str, User], user_id: str) -> User:
    return users_by_id[user_id]
```

---

### 2. Name Variables Based on What They Store (Be Pedantic)

**Why it matters:** A variable name should describe exactly what the variable holds,
respecting singular vs. plural forms (e.g., `datum` vs. `data`, `entry` vs. `entries`).

**Bad:**
```python
result = database.query("SELECT * FROM users")
data = fetch_api()
```

**Good:**
```python
user_records = database.query("SELECT * FROM users")
api_response_payload = fetch_api()
```

**Flag these vague names:** `data` (when generic), `item`, `value`, `result`, `obj`, `info`, `thing`, `stuff`

---

### 3. Concept Chunking (Raymond Hettinger)

**Why it matters:** Functions and methods should operate at a single, consistent level of abstraction (SLAP). Break complex logic into bite-sized, cohesive conceptual chunks where each step does one clear job.

**Bad:**
```python
def process_user_batches(batch_records):
    for batch in batch_records:
        if batch.get("is_active") and not batch.get("is_deleted") and batch.get("count", 0) > 0:
            for record in batch.get("records", []):
                fields = record.split(",")
                user_id = int(fields[0].strip())
                user_email = fields[1].strip()
                database.execute("UPDATE users SET email = ? WHERE id = ?", (user_email, user_id))
                send_notification_email(user_email, "Updated")
```

**Good:**
```python
def is_valid_batch(batch_record: dict) -> bool:
    return batch_record.get("is_active") and not batch_record.get("is_deleted") and batch_record.get("count", 0) > 0


def parse_user_record(raw_record_text: str) -> tuple[int, str]:
    fields = [field_text.strip() for field_text in raw_record_text.split(",")]
    return int(fields[0]), fields[1]


def process_user_batches(batch_list: list[dict]) -> None:
    for batch_record in filter(is_valid_batch, batch_list):
        for raw_record_text in batch_record.get("records", []):
            user_id, user_email = parse_user_record(raw_record_text)
            update_user_email(user_id, user_email)
            send_notification_email(user_email, "Updated")
```

---

### 4. No Silent Exception Swallowing (Always Log or Deliberately Suppress)

**Why it matters:** Silently catching and discarding broad exceptions with `except Exception: pass` destroys debuggability and hides root causes. Catch specific exception types and log context or use `contextlib.suppress(...)` only for narrow, expected OS/cleanup conditions.

**Bad:**
```python
try:
    user_configuration = load_user_config(config_file_path)
except Exception:
    pass
```

**Good:**
```python
try:
    user_configuration = load_user_config(config_file_path)
except OSError as configuration_error:
    logger.error("Failed to load user config from %s: %s", config_file_path, configuration_error, exc_info=True)
    user_configuration = get_default_config()
```

---

## Naming Conventions (PEP 8)

### 5. Casing Rules

- **Classes / Exceptions / Type Aliases:** `PascalCase` (e.g., `SessionManager`, `ToolResult`)
- **Functions, Methods, Parameters, and Variables:** `snake_case` (e.g., `get_user_records()`, `user_records`)
- **Module-Level Constants:** `UPPER_SNAKE_CASE` (e.g., `DEFAULT_TIMEOUT_SECONDS`)

### 6. Boolean Naming: Prefix Booleans with `is_`, `has_`, `can_`, `should_`

**Bad:**
```python
valid = check_password(raw_password)
visible = user.permissions.admin
```

**Good:**
```python
is_valid = check_password(raw_password)
has_admin_permissions = user.permissions.admin
```

### 7. Functions Are Verbs, Variables Are Nouns

**Bad:**
```python
user_get(user_id)
result_calculation = compute()
```

**Good:**
```python
get_user(user_id)
calculation_summary = compute()
```

### 8. No Cryptic Abbreviations (Spell It Out)

**Bad:**
```python
usr = get_current_user()
msg = "Hello"
```

**Good:**
```python
current_user = get_current_user()
greeting_message = "Hello"
```

**Exceptions:** Standard domain terms like `id`, `url`, `api`, `http`, `https`, `json`, `ast`, `io`

---

## Pythonic Patterns

### 9. EAFP vs LBYL (Easier to Ask For Forgiveness than Permission)

```python
resolved_value = lookup_table.get(target_key, default_value)
```

### 10. Comprehensions & Generator Expressions Over Manual Loops

```python
squares = [number**2 for number in range(10)]
even_numbers = [number for number in range(20) if number % 2 == 0]
total_squared = sum(number**2 for number in range(1_000_000))
```

### 11. Context Managers (`with` Statements)

```python
with open("input.txt", encoding="utf-8") as input_file, open("output.txt", "w", encoding="utf-8") as output_file:
    output_file.write(input_file.read())
```

### 12. Use `heapq`, `defaultdict`, `Counter`, and Sequence Unpacking

```python
import heapq
from collections import Counter, defaultdict

def get_top_ten(records: list[dict], sort_key) -> list[dict]:
    return heapq.nlargest(10, records, key=sort_key)

first_entry, second_entry, *remaining_entries = records
word_counts = Counter(document_text.split())
grouped_entries: dict[str, list[str]] = defaultdict(list)
```

---

## Quick Reference Card

| Pattern | Bad | Good |
|---------|-----|------|
| Single-letter vars | `x`, `d`, `i` | `number`, `users_by_id`, `index` |
| Casing | `userData`, `isValid` | `user_data`, `is_valid` |
| Vague names | `data`, `result` | `user_records`, `validation_outcome` |
| Function naming | `user_get` | `get_user` |
| Loop pattern | `for i in range(limit): out.append(i * 2)` | `[number * 2 for number in range(limit)]` |
| File handling | `handle = open(...); handle.close()` | `with open(..., encoding="utf-8") as file_handle:` |
| Dict access | `if key in mapping: val = mapping[key]` | `resolved_val = mapping.get(key, default_val)` |
| Top-K selection | `sorted(records)[:10]` | `heapq.nlargest(10, records)` |
