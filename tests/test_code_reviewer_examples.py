from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dendrophis.events import TextDeltaEvent
from dendrophis.subagents.handlers.code_reviewer import (
    CODE_REVIEWER_SYSTEM_PROMPT,
    CodeReviewerHandler,
)
from dendrophis.subagents.messages import SubagentRequest

# ---------------------------------------------------------------------------
# Bad Code Examples (Violates Hettinger naming, concept chunking & Greybeard robustness)
# ---------------------------------------------------------------------------

BAD_CODE_SINGLE_LETTER_AND_RESOURCE_LEAK = """
def process_data(d):
    f = open("output.log", "a")
    try:
        for i, x in enumerate(d):
            if x > 10:
                f.write(f"{i}: {x}\\n")
    except Exception as e:
        pass
"""

BAD_CODE_MONOLITHIC_UNCHUNKED = """
def handle_incoming_orders(raw_order_batches):
    for batch_data in raw_order_batches:
        if batch_data.get("is_valid") and not batch_data.get("is_archived") and len(batch_data.get("items", [])) > 0:
            for item_line in batch_data.get("items", []):
                tokens = item_line.split("::")
                item_identifier = int(tokens[0].strip())
                item_price = float(tokens[1].strip())
                discount_rate = 0.05 if item_price > 100 else 0.0
                final_price = item_price * (1.0 - discount_rate)
                connection = database_driver.connect("db://orders")
                cursor = connection.cursor()
                cursor.execute(f"INSERT INTO order_items VALUES ({item_identifier}, {final_price})")
                connection.commit()
"""

# ---------------------------------------------------------------------------
# Good Code Examples (Hettinger Concept Chunking + Descriptive Naming + Greybeard Robustness)
# ---------------------------------------------------------------------------

GOOD_CODE_HETTINGER_CONCEPT_CHUNKED = """
def is_valid_order_batch(batch_datum: dict[str, Any]) -> bool:
    return (
        batch_datum.get("is_valid", False)
        and not batch_datum.get("is_archived", False)
        and bool(batch_datum.get("items"))
    )

def parse_order_item(raw_item_line: str) -> tuple[int, float]:
    tokens = [token.strip() for token in raw_item_line.split("::")]
    item_identifier = int(tokens[0])
    raw_item_price = float(tokens[1])
    return item_identifier, raw_item_price

def calculate_discounted_price(item_price: float) -> float:
    discount_multiplier = 0.95 if item_price > 100.0 else 1.0
    return item_price * discount_multiplier

def process_order_batches(order_batches: list[dict[str, Any]], database_connection: Any) -> None:
    valid_batches = filter(is_valid_order_batch, order_batches)
    for batch_datum in valid_batches:
        for raw_item_line in batch_datum.get("items", []):
            item_identifier, raw_item_price = parse_order_item(raw_item_line)
            final_price = calculate_discounted_price(raw_item_price)
            with database_connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO order_items (item_id, price) VALUES (%s, %s)",
                    (item_identifier, final_price),
                )
"""


@pytest.mark.anyio
async def test_code_reviewer_flags_bad_code_examples() -> None:
    captured_messages = []
    mock_llm_client = MagicMock()

    mock_review_response = {
        "approval": "changes_requested",
        "summary": (
            "Multiple blockers identified: single-letter variable names, "
            "unhandled resource leaks, and bare exception swallowing."
        ),
        "issues": [
            {
                "severity": "blocker",
                "file": "dendrophis/processor.py",
                "line": 2,
                "description": "Single-letter variable names 'd', 'f', 'i', 'x', 'e' violate Hettinger standards.",
                "suggestion": "Rename to descriptive variable names (e.g. data_items, log_file_handle).",
            },
            {
                "severity": "blocker",
                "file": "dendrophis/processor.py",
                "line": 3,
                "description": (
                    "File handle is opened without a context manager (`with open(...)`), "
                    "causing resource leaks on exception."
                ),
                "suggestion": "Use `with open('output.log', 'a', encoding='utf-8') as log_file_handle:`.",
            },
            {
                "severity": "warning",
                "file": "dendrophis/processor.py",
                "line": 8,
                "description": "Bare `pass` in exception handler silently swallows errors.",
                "suggestion": "Log the error or re-raise with informative context.",
            },
        ],
        "hettinger_notes": [
            "Reject single-letter variables: 'd', 'f', 'i', 'x', 'e'.",
            "Use context manager for file I/O.",
        ],
        "greybeard_notes": [
            "Resource leak: open file descriptor will hang if an exception occurs before close.",
            "Silent failure: catching all exceptions and passing destroys debuggability.",
        ],
    }

    async def mock_stream_chat(messages_list):
        captured_messages.extend(messages_list)
        yield TextDeltaEvent(delta=json.dumps(mock_review_response))

    mock_llm_client.stream_chat = mock_stream_chat
    handler = CodeReviewerHandler(llm_client=mock_llm_client)

    request = SubagentRequest(
        agent="code-reviewer",
        task_id="task_bad_code_01",
        payload={
            "diff": BAD_CODE_SINGLE_LETTER_AND_RESOURCE_LEAK,
            "task": "Review processor implementation",
        },
        context={},
    )

    response = await handler.execute(request)

    assert response.status == "success"
    assert response.result.get("approval") == "changes_requested"
    assert len(response.result.get("issues", [])) == 3
    assert len(response.result.get("hettinger_notes", [])) == 2
    assert len(response.result.get("greybeard_notes", [])) == 2

    # Verify prompt carries the core review rules
    system_prompt_content = captured_messages[0]["content"]
    assert "one job per function" in system_prompt_content
    assert "Single-letter variable" in system_prompt_content
    assert "greybeard" in system_prompt_content


@pytest.mark.anyio
async def test_code_reviewer_approves_good_code_examples() -> None:
    captured_messages = []
    mock_llm_client = MagicMock()

    mock_review_response = {
        "approval": "approved",
        "summary": (
            "Excellent implementation demonstrating clean concept chunking, "
            "descriptive naming, and robust parameterized DB operations."
        ),
        "issues": [],
        "hettinger_notes": [
            "Great concept chunking with single-purpose helpers: is_valid_order_batch, parse_order_item.",
            "Descriptive variable naming throughout.",
        ],
        "greybeard_notes": [
            "Robust parameterized SQL query prevents injection.",
            "Cursor management uses safe context manager.",
        ],
    }

    async def mock_stream_chat(messages_list):
        captured_messages.extend(messages_list)
        yield TextDeltaEvent(delta=json.dumps(mock_review_response))

    mock_llm_client.stream_chat = mock_stream_chat
    handler = CodeReviewerHandler(llm_client=mock_llm_client)

    request = SubagentRequest(
        agent="code-reviewer",
        task_id="task_good_code_01",
        payload={
            "diff": GOOD_CODE_HETTINGER_CONCEPT_CHUNKED,
            "task": "Review modular order processing pipeline",
        },
        context={},
    )

    response = await handler.execute(request)

    assert response.status == "success"
    assert response.result.get("approval") == "approved"
    assert len(response.result.get("issues", [])) == 0
    assert "Great concept chunking" in response.result["hettinger_notes"][0]
