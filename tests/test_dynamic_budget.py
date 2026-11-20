from pairtner.core.context_composer import (
    DEFAULT_BUDGETS, ContextComposer, compute_budgets,
)


def test_4k_model_gets_minimum_budget():
    assert compute_budgets(4096) == DEFAULT_BUDGETS
    assert compute_budgets(2048) == DEFAULT_BUDGETS


def test_8k_model_gets_50pct_more():
    # Formula min(2.5, max(1, tokens/4096)) gives 2.0x at 8K; 1.5x at 6K.
    b = compute_budgets(6144)
    assert sum(b.values()) >= int(sum(DEFAULT_BUDGETS.values()) * 1.45)
    b8 = compute_budgets(8192)
    assert sum(b8.values()) > sum(b.values())


def test_32k_model_caps_at_2_5x():
    b = compute_budgets(32768)
    assert b == {k: int(v * 2.5) for k, v in DEFAULT_BUDGETS.items()}
    assert compute_budgets(1_000_000) == b


def test_missing_context_uses_default():
    assert ContextComposer("x.db").budgets == DEFAULT_BUDGETS
    assert ContextComposer("x.db", context_tokens=None).budgets == DEFAULT_BUDGETS
    assert ContextComposer("x.db", context_tokens=0).budgets == DEFAULT_BUDGETS
    assert ContextComposer("x.db", context_tokens=16384).budgets != DEFAULT_BUDGETS


def test_section_ratios_preserved():
    b = compute_budgets(16384)
    assert set(b) == set(DEFAULT_BUDGETS)
    for k, v in DEFAULT_BUDGETS.items():
        assert abs(b[k] / b["working"] - v / DEFAULT_BUDGETS["working"]) < 0.02
    assert b["working"] >= b["summaries"] >= b["partner"]
