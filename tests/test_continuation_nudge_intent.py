r"""Behavior-pinning tests for ChatOrchestrator._signals_unfinished_intent.

The stall-recovery nudge (config.llm.continuation_nudge) fires when the model
stops with text that narrates an action it never took ("let me read the file..."
-> stop, no tool call). This heuristic must stay a precision-first keyword
match: it triggers on an intent intro phrase immediately followed by an action
verb, and must NOT trigger on normal closings ("let me know if you need
anything else", "I'll be here if you need me") or phrasal-verb closings
("I'll read back the file").

Implementation: a single compiled regex
``\b(?:intros)\s+(?:verbs)\b(?!\s+back\b)`` — one alternation instead of
per-(intro, verb) pattern pairs. The leading ``\b`` stops "i'll" from matching
inside "still"/"will"/"skill"; the per-position lookahead suppresses only the
specific "<verb> back" match at that position, so a later genuine intent in
the same text still triggers.
"""

from __future__ import annotations

import pytest

from dendrophis.session.chat import ChatOrchestrator


def signals(text: str) -> bool:
    return ChatOrchestrator._signals_unfinished_intent(text)


TRIGGER_CASES = [
    # Basic: intro phrase immediately followed by an action verb.
    "let me read the file",
    "I'll check the logs",
    "I'm going to run the tests",
    "Let's look at this",
    "I need to search the codebase",
    "I should verify the output",
    "I'll go ahead and open the file",
    "Let us find the bug",
    "I will create the file",
    "going to scan the directory",
    # Case-insensitive (input is lowercased).
    "Now let me read the file.",
    "LET ME READ THE FILE",
    # Surrounding whitespace is stripped before the tail window.
    "   let me read the file   ",
    # Trailing punctuation is stripped so the verb still matches.
    "let me read the file...",
    # "read ... back" with words in between is NOT the phrasal verb "read back".
    "I'll read the file back",
    # Per-position exclusion: "i'll read back" is suppressed at that position,
    # but a later genuine intent ("let me check") in the same tail still triggers.
    "I'll read back the file, then let me check the config",
    # Intent phrase within the 200-char tail window still triggers.
    "let me read the file " + "x" * 100,
    # Per-position exclusion: only the first "check back" match is suppressed;
    # the second "I'll check" still triggers (a whole-pair exclusion would not).
    "I'll check back later; first I'll check the logs",
]

NO_TRIGGER_CASES = [
    # Empty input.
    "",
    # Normal closings: the word after the intro is not an action verb.
    "let me know if you need anything else",
    "I'll be here if you need me",
    # Phrasal-verb closings: "<intro> <verb> back" is excluded at that position,
    # and no other intro+verb match exists.
    "I'll read back the file",
    "Let me look back at that",
    "I'll check back with you tomorrow",
    "i'll go back to normal",
    # Verb not immediately after the intro (intervening word breaks the match).
    "I'll start by reading the file",
    "let me to read the file",
    # No intro phrase at all.
    "read the file",
    # \b word boundary: "reading" must not match the verb "read".
    "let me reading the file",
    # Intent phrase outside the 200-char tail window is ignored.
    "let me read the file " + "x" * 250,
    # Leading \b: "i'll" must not match inside other words ("still", "skill",
    # "will").
    "Things still look good.",
    "My skill test passed",
    "Things will look good.",
]


@pytest.mark.parametrize("text", TRIGGER_CASES)
def test_signals_unfinished_intent_triggers(text: str) -> None:
    assert signals(text) is True


@pytest.mark.parametrize("text", NO_TRIGGER_CASES)
def test_signals_unfinished_intent_does_not_trigger(text: str) -> None:
    assert signals(text) is False
