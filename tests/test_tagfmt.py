"""Tag format helpers: JSON array (new) and plain string (legacy)."""
import pytest

from pairtner.core.summary import _tag_of
from pairtner.storage.tagfmt import encode_tags, first_tag, parse_fact_tags


@pytest.mark.parametrize("stored,expected", [
    (None, []), ("", []), ("kids", ["kids"]), ('["kids", "school"]', ["kids", "school"]),
    ("[]", []), ("[bad json", ["[bad json"]), (["A", "a"], ["a"]),
])
def test_parse(stored, expected):
    assert parse_fact_tags(stored) == expected


def test_encode_and_first():
    assert encode_tags(["kids", "school"]) == '["kids", "school"]'
    assert encode_tags("kids") == '["kids"]'
    assert encode_tags(None) is None
    assert first_tag('["kids", "school"]') == "kids"
    assert first_tag("work") == "work"


def test_tag_of_both_formats():
    assert _tag_of({"tag": '["kids", "school"]'}) == "kids"
    assert _tag_of({"tag": "kids"}) == "kids"
    assert _tag_of({"tag": None}) == "other" or _tag_of({"tag": None})
