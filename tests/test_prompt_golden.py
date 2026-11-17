"""Every model-visible prompt must stay byte-identical to the v0.1.0 snapshot."""
from __future__ import annotations

import difflib
import json

from tests.golden_prompts import GOLDEN_PATH, collect


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
    expected = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    actual = await collect()

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
