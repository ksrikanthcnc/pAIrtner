"""Every model-visible prompt must stay byte-identical to the v0.1.0 snapshot."""
from __future__ import annotations

import difflib
import json
import re

from tests.golden_prompts import GOLDEN_PATH, collect

# The "Current time:" line is dynamic — normalize it before comparison.
_TIME_RE = re.compile(r"Current time: \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC")
_TIME_PLACEHOLDER = "Current time: XXXX-XX-XX XX:XX UTC"
# Relative timestamps like "[3h ago]", "[5m ago]", "[2d ago]", "[yesterday]", "[1w ago]", "[2mo ago]"
_REL_TIME_RE = re.compile(r"\[(?:just now|\d+[mhdw] ago|yesterday|\d+mo ago)\]")
_REL_PLACEHOLDER = "[REL_TIME]"
# Absolute timestamps in history like "[2026-11-19 02:43]"
_ABS_TIME_RE = re.compile(r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\]")
_ABS_PLACEHOLDER = "[XXXX-XX-XX XX:XX]"
# Gap markers like "--- 2 hours gap ---"
_GAP_RE = re.compile(r"--- \d+ (?:min|hours?|days?) gap ---")
_GAP_PLACEHOLDER = "--- GAP ---"
# Camouflage check-in questions are randomly injected; strip the whole section.
_CAMOUFLAGE_RE = re.compile(
    r"\n## Optional check-in \(ask naturally, at most once, only if it fits the flow\)\n- .+",
    re.DOTALL,
)


def _normalize(prompts: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """Replace dynamic time stamps so golden comparison is stable."""
    out: dict[str, dict[str, str]] = {}
    for case_id, parts in prompts.items():
        out[case_id] = {}
        for k, v in parts.items():
            v = _TIME_RE.sub(_TIME_PLACEHOLDER, v)
            v = _REL_TIME_RE.sub(_REL_PLACEHOLDER, v)
            v = _ABS_TIME_RE.sub(_ABS_PLACEHOLDER, v)
            v = _GAP_RE.sub(_GAP_PLACEHOLDER, v)
            v = _CAMOUFLAGE_RE.sub("", v)
            v = v.rstrip()
            out[case_id][k] = v
    return out


def _diff(case_id: str, expected: dict, actual: dict) -> str:
    chunks = []
    for part in ("system", "user"):
        if expected.get(part) != actual.get(part):
            chunks.extend(difflib.unified_diff(
                (expected.get(part) or "").splitlines(keepends=True),
                (actual.get(part) or "").splitlines(keepends=True),
                fromfile=f"{case_id}:{part} (golden)",
                tofile=f"{case_id}:{part} (now)",
            ))
    return "".join(chunks)


async def test_prompts_match_golden_snapshot():
    expected = _normalize(json.loads(GOLDEN_PATH.read_text(encoding="utf-8")))
    actual = _normalize(await collect())

    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    assert not missing, f"cases missing from current output: {missing}"
    assert not extra, f"cases not in the golden snapshot: {extra}"

    for case_id in sorted(expected):
        if expected[case_id] != actual[case_id]:
            raise AssertionError(
                f"prompt changed for case {case_id!r}\n"
                + _diff(case_id, expected[case_id], actual[case_id])
            )
    assert actual == expected
