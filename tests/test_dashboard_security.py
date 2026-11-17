"""Auth helpers and HTML rendering."""
from __future__ import annotations

from pairtner.dashboard.render import Html, layout, svg_bars, table
from pairtner.dashboard.security import BEARER_SESSION, Authenticator

TOKEN = "super-secret-token-0123456789"


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def make(clock=None, **kw):
    return Authenticator(TOKEN, 3600, clock=clock or Clock(), **kw)


def test_token_check():
    a = make()
    assert a.check_token(TOKEN)
    assert not a.check_token("")
    assert not a.check_token(TOKEN + "x")
    assert not a.check_token(TOKEN[:-1])


def test_session_roundtrip_and_no_token_in_cookie():
    a = make()
    cookie = a.issue_session()
    assert a.verify_session(cookie)
    assert TOKEN not in cookie


def test_tampered_and_garbage_sessions_rejected():
    a = make()
    exp, _, sig = a.issue_session().partition(".")
    for bad in (None, "", "garbage", f"{int(exp) + 999}.{sig}", f"{exp}.{'0' * 64}",
                f"abc.{sig}", f"{exp}."):
        assert not a.verify_session(bad)


def test_session_from_other_token_rejected():
    other = Authenticator("another-token-0123456789", 3600, clock=Clock())
    assert not make().verify_session(other.issue_session())


def test_session_expires():
    clock = Clock()
    a = make(clock)
    cookie = a.issue_session()
    clock.now += 3599
    assert a.verify_session(cookie)
    clock.now += 2
    assert not a.verify_session(cookie)


def test_csrf():
    a = make()
    s = a.issue_session()
    t = a.csrf_token(s)
    assert a.verify_csrf(s, t)
    assert not a.verify_csrf(s, None) and not a.verify_csrf(s, "")
    assert not a.verify_csrf(s, "x" * len(t))
    assert not a.verify_csrf(BEARER_SESSION, t)
    assert a.verify_csrf(BEARER_SESSION, a.csrf_token(BEARER_SESSION))


def test_lockout_after_five_failures_then_expires():
    clock = Clock()
    a = make(clock)
    for _ in range(4):
        a.record_failure("1.1.1.1")
    assert not a.is_locked("1.1.1.1")
    a.record_failure("1.1.1.1")
    assert a.is_locked("1.1.1.1")
    assert not a.is_locked("2.2.2.2")
    clock.now += 301
    assert not a.is_locked("1.1.1.1")


def test_success_resets_failures():
    a = make()
    for _ in range(4):
        a.record_failure("c")
    a.record_success("c")
    a.record_failure("c")
    assert not a.is_locked("c")


def test_render_escapes_untrusted_text():
    payload = "<script>alert(1)</script>"
    out = table(["a<b"], [[payload], [Html("<b>ok</b>")]])
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert "<b>ok</b>" in out and "a&lt;b" in out
    page = layout(payload, "", "/", csrf=payload)
    assert "<script>" not in page


def test_table_truncates_long_cells():
    assert "x" * 1000 not in table(["a"], [["x" * 1000]])


def test_svg_bars():
    svg = svg_bars([("a<", 1), ("b", 3)])
    assert svg.startswith("<svg") and svg.count("<rect") == 2 and "a&lt;" in svg
    assert "no data" in svg_bars([])
