"""Input sanitization — strip common prompt-injection patterns."""

from __future__ import annotations

import re

# Compiled regexes: (pattern_name, compiled_regex)
_INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ignore_previous", re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE)),
    ("system_prompt", re.compile(r"system\s+prompt\s*:", re.IGNORECASE)),
    ("you_are_now", re.compile(r"you\s+are\s+now\b", re.IGNORECASE)),
    ("repeat_above", re.compile(r"repeat\s+everything\s+above", re.IGNORECASE)),
    ("forget_instructions", re.compile(r"forget\s+your\s+instructions", re.IGNORECASE)),
    ("disregard_all", re.compile(r"disregard\s+all\b", re.IGNORECASE)),
]


def sanitize_input(text: str) -> tuple[str, list[str]]:
    """Strip injection patterns from user input.

    Returns ``(cleaned_text, list_of_stripped_pattern_names)``.

    If the entire message is an injection attempt (nothing left after
    stripping), the original text is returned unchanged.
    """
    cleaned = text
    stripped: list[str] = []

    for name, pattern in _INJECTION_PATTERNS:
        if pattern.search(cleaned):
            stripped.append(name)
            cleaned = pattern.sub("", cleaned)

    # Collapse extra whitespace left by removals
    cleaned = re.sub(r"  +", " ", cleaned).strip()

    # If nothing meaningful remains, return empty — caller decides response
    return cleaned, stripped
