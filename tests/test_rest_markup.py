"""Tests for pairtner.rest.markup (safe rendering of bot text)."""
from __future__ import annotations

import pytest

from pairtner.rest.markup import to_markup, to_plain
from pairtner.rest.throttle import SlidingWindowLimiter


def test_whitelisted_tags_kept():
    assert to_markup("a <b>bold</b> <i>it</i> <u>u</u> <code>c</code>") == (
        "a <b>bold</b> <i>it</i> <u>u</u> <code>c</code>"
    )
    assert to_markup("<pre>x</pre>") == "<pre>x</pre>"


def test_script_is_escaped():
    out = to_markup("hi <script>alert(1)</script>")
    assert "<script" not in out
    assert "&lt;script&gt;" in out


def test_img_onerror_is_escaped():
    out = to_markup('<img src=x onerror="alert(1)">')
    assert "<img" not in out
    assert out.startswith("&lt;img")


@pytest.mark.parametrize("raw", [
    '<b onclick="evil()">x</b>',
    "<b style='x:y'>x</b>",
    '<code class="a" onmouseover=1>x</code>',
])
def test_attributes_dropped_on_whitelisted_tags(raw):
    out = to_markup(raw)
    assert "onclick" not in out and "onmouseover" not in out and "style" not in out
    assert out.startswith("<b>") or out.startswith("<code>")


def test_self_closing_variant_is_not_a_tag():
    assert "<b/>" not in to_markup("<b/>")


def test_ampersand_and_angle_brackets_escaped():
    assert to_markup("a & b < c > d") == "a &amp; b &lt; c &gt; d"


def test_entity_round_trip_single_level():
    out = to_markup("1 &lt; 2 &amp; 3 &gt; 2")
    assert out == "1 &lt; 2 &amp; 3 &gt; 2"
    assert to_plain("1 &lt; 2 &amp; 3 &gt; 2") == "1 < 2 & 3 > 2"


def test_unbalanced_tags_are_closed():
    assert to_markup("<b>x <i>y") == "<b>x <i>y</i></b>"


def test_stray_closing_tag_dropped():
    assert to_markup("x</b>y") == "xy"


def test_mis_nested_closes_in_order():
    assert to_markup("<b><i>x</b>y") == "<b><i>x</i></b>y"


def test_case_insensitive_tags_normalised():
    assert to_markup("<B>x</B>") == "<b>x</b>"


def test_plain_strips_whitelisted_tags_and_decodes():
    assert to_plain("<b>Hi</b> &amp; <i>bye</i>") == "Hi & bye"


def test_plain_text_passthrough_and_telugu():
    assert to_plain("నమస్తే") == "నమస్తే"
    assert to_markup("నమస్తే <b>x</b>") == "నమస్తే <b>x</b>"


def test_limiter_blocks_after_limit_and_recovers():
    now = [0.0]
    lim = SlidingWindowLimiter(2, 10.0, clock=lambda: now[0])
    assert lim.hit("ip") and lim.hit("ip")
    assert not lim.hit("ip")
    assert lim.blocked("ip")
    assert lim.hit("other")
    now[0] = 10.5
    assert not lim.blocked("ip")
    assert lim.hit("ip")
