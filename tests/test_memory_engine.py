"""Tests for pairtner.memory — ops parsing, validation, pin detection, dedup."""

from __future__ import annotations

import pytest

from pairtner.core.memory import (
    DEFAULT_CORE_PHRASES,
    MemoryOp,
    _normalise_text,
    format_memory_line,
    is_explicit_core_request,
    is_near_duplicate,
    parse_memory_ops,
)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

class TestNormalise:
    def test_casefold(self):
        assert _normalise_text("Hello WORLD") == "hello world"

    def test_strip_punctuation(self):
        assert _normalise_text("don't forget!") == "dont forget"

    def test_collapse_spaces(self):
        assert _normalise_text("  too   many   spaces  ") == "too many spaces"


# ---------------------------------------------------------------------------
# Near-duplicate detection
# ---------------------------------------------------------------------------

class TestNearDuplicate:
    def test_exact_match(self):
        assert is_near_duplicate("Son is 6", "Son is 6")

    def test_case_insensitive(self):
        assert is_near_duplicate("Son is 6", "son is 6")

    def test_similar_text(self):
        assert is_near_duplicate("Son Arjun is 6 years old", "Son Arjun is 6", threshold=0.7)

    def test_different_text(self):
        assert not is_near_duplicate("Son is 6", "Deadline on Friday")

    def test_updated_version(self):
        # "Son is 7" vs "Son is 6" — close but different info
        # This is intentionally borderline — the threshold matters
        assert is_near_duplicate("Son is 7", "Son is 6", threshold=0.7)


# ---------------------------------------------------------------------------
# Explicit core request detection
# ---------------------------------------------------------------------------

class TestExplicitCore:
    def test_remember_command(self):
        assert is_explicit_core_request("/remember I'm an introvert")

    def test_remember_tag(self):
        assert is_explicit_core_request("I'm an introvert #remember")

    def test_phrase_english(self):
        assert is_explicit_core_request("Remember this about me: I need quiet time")

    def test_phrase_telugu(self):
        assert is_explicit_core_request("ఇది నా గురించి గుర్తుంచుకో")

    def test_normal_message(self):
        assert not is_explicit_core_request("I had a bad day at work")

    def test_ambiguous_remember(self):
        # "remember" alone isn't enough — needs the phrase
        assert not is_explicit_core_request("I remember when we first met")

    def test_custom_phrases(self):
        assert is_explicit_core_request(
            "save this forever",
            phrases=["save this forever"],
        )

    def test_case_insensitive(self):
        assert is_explicit_core_request("REMEMBER THIS ABOUT ME: I'm vegan")


# ---------------------------------------------------------------------------
# Op parsing
# ---------------------------------------------------------------------------

class TestParseOps:
    def test_valid_add(self):
        raw = [{"op": "add", "kind": "working", "text": "Deadline Friday"}]
        ops = parse_memory_ops(raw, user_fact_ids=set())
        assert len(ops) == 1
        assert ops[0].op == "add"
        assert ops[0].kind == "working"
        assert ops[0].text == "Deadline Friday"

    def test_add_core_downgraded(self):
        """Model can't add core directly — downgraded to propose_core."""
        raw = [{"op": "add", "kind": "core", "text": "Introvert"}]
        ops = parse_memory_ops(raw, user_fact_ids=set())
        assert len(ops) == 1
        assert ops[0].op == "propose_core"

    def test_add_invalid_kind_defaults_working(self):
        raw = [{"op": "add", "kind": "secret", "text": "Test"}]
        ops = parse_memory_ops(raw, user_fact_ids=set())
        assert ops[0].kind == "working"

    def test_add_empty_text_rejected(self):
        raw = [{"op": "add", "kind": "working", "text": ""}]
        ops = parse_memory_ops(raw, user_fact_ids=set())
        assert len(ops) == 0

    def test_add_visibility_ignored(self):
        """Model can't set visibility — everything is private."""
        raw = [{"op": "add", "kind": "working", "text": "Test", "visibility": "shared"}]
        ops = parse_memory_ops(raw, user_fact_ids=set())
        assert len(ops) == 1
        # MemoryOp has no visibility field — forced private in apply

    def test_revise_own_fact(self):
        raw = [{"op": "revise", "id": 42, "text": "Son is 7"}]
        ops = parse_memory_ops(raw, user_fact_ids={42, 43})
        assert len(ops) == 1
        assert ops[0].op == "revise"
        assert ops[0].id == 42

    def test_revise_foreign_id_rejected(self):
        raw = [{"op": "revise", "id": 99, "text": "Hacked"}]
        ops = parse_memory_ops(raw, user_fact_ids={42, 43})
        assert len(ops) == 0

    def test_revise_no_id_rejected(self):
        raw = [{"op": "revise", "text": "No id"}]
        ops = parse_memory_ops(raw, user_fact_ids={42})
        assert len(ops) == 0

    def test_status_valid(self):
        raw = [{"op": "status", "id": 42, "status": "resolved", "why": "Done"}]
        ops = parse_memory_ops(raw, user_fact_ids={42})
        assert len(ops) == 1
        assert ops[0].status == "resolved"
        assert ops[0].why == "Done"

    def test_status_invalid_value(self):
        raw = [{"op": "status", "id": 42, "status": "deleted"}]
        ops = parse_memory_ops(raw, user_fact_ids={42})
        assert len(ops) == 0

    def test_propose_core(self):
        raw = [{"op": "propose_core", "text": "Introvert"}]
        ops = parse_memory_ops(raw, user_fact_ids=set())
        assert len(ops) == 1
        assert ops[0].op == "propose_core"

    def test_pin_core(self):
        raw = [{"op": "pin_core", "text": "Needs quiet time"}]
        ops = parse_memory_ops(raw, user_fact_ids=set())
        assert len(ops) == 1
        assert ops[0].op == "pin_core"

    def test_unknown_op_rejected(self):
        raw = [{"op": "delete", "id": 42}]
        ops = parse_memory_ops(raw, user_fact_ids={42})
        assert len(ops) == 0

    def test_remove_op_rejected(self):
        raw = [{"op": "remove", "id": 42}]
        ops = parse_memory_ops(raw, user_fact_ids={42})
        assert len(ops) == 0

    def test_max_ops_cap(self):
        raw = [{"op": "add", "kind": "working", "text": f"Fact {i}"} for i in range(10)]
        ops = parse_memory_ops(raw, user_fact_ids=set(), max_ops=5)
        assert len(ops) == 5

    def test_non_list_returns_empty(self):
        ops = parse_memory_ops("not a list", user_fact_ids=set())
        assert ops == []

    def test_none_returns_empty(self):
        ops = parse_memory_ops(None, user_fact_ids=set())
        assert ops == []

    def test_non_dict_items_skipped(self):
        raw = [{"op": "add", "kind": "working", "text": "OK"}, "not a dict", 42]
        ops = parse_memory_ops(raw, user_fact_ids=set())
        assert len(ops) == 1

    def test_string_id_parsed(self):
        """Model might send id as string instead of int."""
        raw = [{"op": "revise", "id": "42", "text": "Updated"}]
        ops = parse_memory_ops(raw, user_fact_ids={42})
        assert len(ops) == 1
        assert ops[0].id == 42

    def test_category_validated(self):
        raw = [{"op": "add", "kind": "working", "text": "Test", "category": "work"}]
        ops = parse_memory_ops(raw, user_fact_ids=set())
        assert ops[0].category == "work"

    def test_invalid_category_nulled(self):
        raw = [{"op": "add", "kind": "working", "text": "Test", "category": "secret"}]
        ops = parse_memory_ops(raw, user_fact_ids=set())
        assert ops[0].category is None


# ---------------------------------------------------------------------------
# Format memory line
# ---------------------------------------------------------------------------

class TestFormatMemoryLine:
    def test_active_with_id(self):
        fact = {"id": 41, "text": "Son is 6", "status": "active"}
        assert format_memory_line(fact) == "[41] Son is 6"

    def test_resolved_with_id(self):
        fact = {"id": 17, "text": "Dishes argument", "status": "resolved"}
        assert format_memory_line(fact) == "[17] (resolved) Dishes argument"

    def test_no_id(self):
        fact = {"text": "Feels unheard", "status": "active"}
        assert format_memory_line(fact, show_id=False) == "- Feels unheard"

    def test_no_status_defaults_active(self):
        fact = {"id": 5, "text": "Test"}
        assert format_memory_line(fact) == "[5] Test"
