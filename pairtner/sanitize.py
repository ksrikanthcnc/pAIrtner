"""Input sanitization — strip common prompt-injection patterns."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Compiled regexes: (pattern_name, compiled_regex)
_INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ignore_previous", re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE)),
    ("system_prompt", re.compile(r"system\s+prompt\s*:", re.IGNORECASE)),
    ("you_are_now", re.compile(r"you\s+are\s+now\b", re.IGNORECASE)),
    ("repeat_above", re.compile(r"repeat\s+everything\s+above", re.IGNORECASE)),
    ("forget_instructions", re.compile(r"forget\s+your\s+instructions", re.IGNORECASE)),
    ("disregard_all", re.compile(r"disregard\s+all\b", re.IGNORECASE)),
]

# Crisis / emergency patterns — word-boundary-guarded to reduce false positives.
# Common false positives like "kill time", "dying to see", "hurt feelings" are
# excluded by requiring specific surrounding context.
_CRISIS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("self_harm_suicide", re.compile(
        r"\b(commit\s+suicide|sui[cx]ide)\b", re.IGNORECASE,
    )),
    ("self_harm_kill_myself", re.compile(
        r"\bkill\s+(my|our)sel(f|ves)\b", re.IGNORECASE,
    )),
    ("self_harm_end_life", re.compile(
        r"\bend\s+my\s+life\b", re.IGNORECASE,
    )),
    ("self_harm_want_to_die", re.compile(
        r"\bwant\s+to\s+die\b", re.IGNORECASE,
    )),
    ("self_harm_generic", re.compile(
        r"\bself[- ]?harm(ing)?\b", re.IGNORECASE,
    )),
    ("self_harm_hurt_myself", re.compile(
        r"\bhurt\s+(my|our)sel(f|ves)\b", re.IGNORECASE,
    )),
    ("abuse_being_hit", re.compile(
        r"\b(being|getting)\s+(hit|beaten|struck)\b", re.IGNORECASE,
    )),
    ("abuse_domestic_violence", re.compile(
        r"\bdomestic\s+violence\b", re.IGNORECASE,
    )),
    ("abuse_abusing_me", re.compile(
        r"\b(is\s+)?abus(es|ing)\s+me\b", re.IGNORECASE,
    )),
    ("threat_going_to_hurt", re.compile(
        r"\b(going|gonna|threatening)\s+to\s+(hurt|harm|kill)\b", re.IGNORECASE,
    )),
    ("threat_threatening_to_kill", re.compile(
        r"\bthreatening\s+to\s+kill\b", re.IGNORECASE,
    )),
    # Suicidal ideation phrased without "die"/"suicide".
    ("self_harm_not_want_to_live", re.compile(
        r"\b(don'?t|do\s+not|no\s+longer)\s+want\s+to\s+(live|be\s+alive|exist|wake\s+up)\b"
        r"|\bno\s+reason\s+to\s+live\b|\bbetter\s+off\s+dead\b",
        re.IGNORECASE,
    )),
    # Physical violence by a PERSON against the speaker. A person subject is
    # required so "the movie hit me hard" does not match.
    ("abuse_person_hits_me", re.compile(
        r"\b(he|she|they|husband|wife|partner|boyfriend|girlfriend|bf|gf|"
        r"fianc[eé]e?|in-?laws?|father|mother|dad|mom|brother|sister)\b"
        r"(?:\W+\w+){0,3}?\W+"
        r"(hit|hits|hitting|slap\w*|beat|beats|beating|beaten|punch\w*|kick\w*|"
        r"chok\w*|strangl\w*|push(?:es|ed|ing)?|shov\w*|smack\w*|burn(?:s|ed|t)?)"
        r"\s+me\b",
        re.IGNORECASE,
    )),
    # Fear of being harmed by someone.
    ("abuse_afraid_of_harm", re.compile(
        r"\b(scared|afraid|terrified|frightened)\b.{0,40}?\b(hurt|hit|harm|kill|beat)\s+me\b",
        re.IGNORECASE,
    )),
]


def detect_crisis(text: str) -> tuple[bool, str | None]:
    """Check text for crisis / emergency language.

    Returns ``(is_crisis, pattern_name)``.  When no crisis is detected,
    returns ``(False, None)``.

    IMPORTANT: This function logs the *pattern name* but NEVER the message
    content to protect user privacy.
    """
    for name, pattern in _CRISIS_PATTERNS:
        if pattern.search(text):
            logger.warning("Crisis language detected — pattern=%s", name)
            return True, name
    return False, None


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
