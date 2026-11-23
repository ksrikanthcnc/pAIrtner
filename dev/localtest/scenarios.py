"""Scripted two-user scenarios over the REST API (stdlib client, no websockets).

Against a running rig (``run_local.py --llm fake`` on the default port):

    uv run python dev/localtest/scenarios.py [--url URL] [--db PATH] [--fake-url URL] [names ...]

Without ``--url`` the rig defaults apply (http://127.0.0.1:8766 and
dev/localtest/test.db). With ``--url`` and no ``--db`` the scenarios that read
the sqlite file (``guards``) are skipped with a notice and DB assertions in the
others are left out. Scenarios that inspect prompts need the fake LLM's
``/_calls`` log (``--fake-url``, default http://127.0.0.1:11435) and are
skipped when it is unreachable. ``relay_no_review`` needs a rig started with
review_before_send=false (``--no-review``) and ``periodic`` needs short loop
intervals, so both are only part of ``run_all.py`` by default. The rig's relay
cool-down (``run_local.py --cooldown``, default 1 minute) is covered by the 150 s relay wait.

Prints a transcript, then ``===== FLAGS =====`` and ``- none`` or the flags.
Exit code 1 if there is any flag. ``run_all.py`` imports this module.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import pathlib
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pairtner.i18n import t  # noqa: E402

DEFAULT_URL = "http://127.0.0.1:8766"
DEFAULT_FAKE_URL = "http://127.0.0.1:11435"
SUSPECT = [r'"reply"\s*:', r"trouble connecting", r"Traceback", r"new_facts", r"relay_draft", r"\bNone\b"]
_TAGS = re.compile(r"<[^>]+>")
_CODE = re.compile(r"\b[0-9A-F]{6}\b")


def plain(text: str) -> str:
    """Strip the HTML-ish markup of an i18n string (the REST text is plain)."""
    return _TAGS.sub("", text).strip()


def head(key: str, **kw: Any) -> str:
    """First plain line of an English i18n string, used to recognise a message."""
    return plain(t("en", key, **kw)).splitlines()[0].strip()


class Skip(Exception):
    """Raised by a scenario that cannot run in this setup (not a flag)."""


class Env:
    """Everything a scenario needs: where the rig is and where to record flags."""

    def __init__(
        self,
        base_url: str,
        *,
        db_path: str | None = None,
        fake_url: str | None = None,
        review: bool = True,
        periodic: dict | None = None,
        relay_wait: float = 150.0,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.db_path = db_path
        self.fake_url = fake_url.rstrip("/") if fake_url else None
        self.review = review
        self.periodic = periodic
        self.relay_wait = relay_wait  # seconds to wait for a relay (>= the rig's cool-down)
        self.flags: list[str] = []
        self.lines: list[str] = []
        self.current = ""
        self._log = log

    def log(self, line: str) -> None:
        self.lines.append(line)
        (self._log or (lambda s: print(s, flush=True)))(line)

    def flag(self, message: str) -> None:
        self.flags.append(f"[{self.current}] {message}")
        self.log(f"   !! FLAG {message}")

    def check(self, cond: object, message: str) -> bool:
        if not cond:
            self.flag(message)
        return bool(cond)

    # ── sqlite (read only) ──────────────────────────────────────────────
    def db(self, sql: str, *params: Any) -> list[tuple]:
        if not self.db_path:
            raise Skip("needs --db (sqlite file of the rig)")
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=10)
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()

    def has_db(self) -> bool:
        return bool(self.db_path)

    # ── fake LLM call log ───────────────────────────────────────────────
    def fake(self, method: str = "GET") -> list[dict]:
        if not self.fake_url:
            raise Skip("needs the fake LLM /_calls log (--fake-url)")
        req = urllib.request.Request(f"{self.fake_url}/_calls", method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())["calls"]
        except OSError as exc:
            raise Skip(f"fake LLM not reachable at {self.fake_url}: {exc}") from exc

    def clear_calls(self) -> None:
        self.fake("DELETE")

    # ── users ───────────────────────────────────────────────────────────
    def pair(self, names: tuple[str, str] = ("Alice", "Bob"), skip: bool = True) -> tuple[Client, Client] | None:
        a, b = Client(self, names[0]), Client(self, names[1])
        a.register()
        b.register()
        r = a.say("/start")
        code = next((c for m in r for c in _CODE.findall(m.get("text", ""))), None)
        self.log(f"   -> pairing code: {code}")
        if not self.check(code, "no pairing code found in /start reply"):
            return None
        join = b.say(f"/join {code}")
        if not self.check(not any(t("en", "join_invalid_code") in m.get("text", "") for m in join),
                          "join code was rejected"):
            return None
        if skip:
            a.say("/skip")
            b.say("/skip")
        a.poll()
        b.poll()
        return a, b


def find_button(msgs: list[dict], prefix: str) -> str | None:
    """Callback data of the first button whose data starts with *prefix*."""
    for m in msgs:
        for row in m.get("buttons") or []:
            for b in row:
                if b["data"].startswith(prefix):
                    return b["data"]
    return None


def button_labels(msgs: list[dict], prefix: str) -> list[str]:
    return [b["text"] for m in msgs for row in m.get("buttons") or [] for b in row if b["data"].startswith(prefix)]


class Client:
    """One REST user: register, send messages, click buttons, poll events."""

    def __init__(self, env: Env, name: str) -> None:
        self.env = env
        self.name = name
        self.uid = 0
        self.token = ""
        self.cursor = 0
        self.inbox: list[dict] = []  # every event msg received, oldest first

    # ── transport ───────────────────────────────────────────────────────
    def request(self, method: str, path: str, body: Any = None, *, auth: bool = True,
                timeout: float = 180.0) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.env.base_url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        try:
            return status, json.loads(raw)
        except json.JSONDecodeError:
            return status, raw

    def register(self, lang: str = "en") -> None:
        status, body = self.request("POST", "/api/v1/register", {"name": self.name, "lang": lang}, auth=False)
        if status != 201:
            self.env.flag(f"{self.name}: register failed {status} {body}")
            raise Skip(f"registration failed ({status}); is the rig running with registration open?")
        self.uid, self.token = body["user_id"], body["token"]
        self.env.log(f"   {self.name} registered as {self.uid}")

    def me(self) -> dict:
        status, body = self.request("GET", "/api/v1/me")
        self.env.check(status == 200, f"{self.name}: GET /me -> {status}")
        return body if isinstance(body, dict) else {}

    # ── conversation ────────────────────────────────────────────────────
    def say(self, text: str) -> list[dict]:
        self.env.log(f"{self.name} > {text}")
        return self._replies(*self.request("POST", "/api/v1/message", {"text": text}))

    def click(self, data: str) -> list[dict]:
        self.env.log(f"{self.name} [click {data}]")
        return self._replies(*self.request("POST", "/api/v1/callback", {"data": data}))

    def _replies(self, status: int, body: Any) -> list[dict]:
        if status != 200 or not isinstance(body, dict):
            self.env.flag(f"{self.name}: HTTP {status} {str(body)[:120]}")
            return []
        replies = body.get("replies", [])
        for m in replies:
            self._show(m, "bot")
        if not replies:
            self.env.flag(f"{self.name}: no reply")
        return replies

    def poll(self, wait: float = 0.0) -> list[dict]:
        """Fetch new events once (long-poll up to *wait* seconds); advance the cursor."""
        status, body = self.request("GET", f"/api/v1/events?after={self.cursor}&wait={wait:g}",
                                    timeout=wait + 60)
        if status != 200 or not isinstance(body, dict):
            self.env.flag(f"{self.name}: events HTTP {status}")
            return []
        self.cursor = body["cursor"]
        msgs = [e["msg"] for e in body["events"]]
        for m in msgs:
            self.inbox.append(m)
            self._show(m, "EVENT")
        return msgs

    def wait_for(self, pred: Callable[[dict], Any], timeout: float = 30.0) -> dict | None:
        """First event (fetched from now on) matching *pred*, or None after *timeout*."""
        end = time.time() + timeout
        while True:
            for m in self.poll(wait=min(3.0, max(0.0, end - time.time()))):
                if pred(m):
                    return m
            if time.time() >= end:
                return None

    def drain(self, seconds: float) -> list[dict]:
        """Collect everything that arrives for a fixed window."""
        end = time.time() + seconds
        out: list[dict] = []
        while time.time() < end:
            out += self.poll(wait=min(2.0, max(0.0, end - time.time())))
        return out

    def _show(self, m: dict, tag: str) -> None:
        text = m.get("text", "") or ""
        btns = ""
        if m.get("buttons"):
            btns = "  [" + " | ".join(f"{b['text']}={b['data']}" for r in m["buttons"] for b in r) + "]"
        self.env.log(f"   {self.name} < {tag}: {text}{btns}")
        for pat in SUSPECT:
            if re.search(pat, text):
                self.env.flag(f"{self.name}: suspicious /{pat}/ in: {text[:80]!r}")


# ── shared steps ─────────────────────────────────────────────────────────

def relay_through(env: Env, a: Client, b: Client, token: str, *, wait: float | None = None) -> dict | None:
    """A drafts ``#send``, approves; returns B's relay event (None + flag if it never came)."""
    wait = env.relay_wait if wait is None else wait
    r = a.say(f"#send {token} you never help with the dishes and I'm sick of it")
    ap = find_button(r, "ap:")
    if not env.check(ap, "no approve button on #send draft"):
        return None
    a.poll()
    a.click(ap)
    if env.review:
        env.log("   waiting for the sender's review prompt...")
        prompt = a.wait_for(lambda m: find_button([m], "ba:"), wait)
        if not env.check(prompt, "no 'Send All' review prompt arrived after the cool-down"):
            return None
        a.click(find_button([prompt], "ba:"))
    got = b.wait_for(lambda m: token in m.get("text", "").upper(), wait)
    env.check(got, "Bob never received the approved relay")
    return got


def only_fact_button(env: Env, owner: Client) -> str | None:
    """Callback data of the single ``fv:`` button on /whatdoyouknow (owner has one fact)."""
    r = owner.say("/whatdoyouknow")
    fv = find_button(r, "fv:")
    env.check(fv, f"{owner.name}: /whatdoyouknow has no share button")
    return fv


# ── scenarios ────────────────────────────────────────────────────────────

def sc_pair_onboard(env: Env) -> None:
    pr = env.pair(skip=False)
    if not pr:
        return
    a, b = pr
    for answer in ("married with kids", "6", "yes", "30s", "her sense of humour and patience",
                   "I wish we had more time for just us"):
        a.say(answer)
    b.say("/skip")
    if env.has_db():
        rows = env.db("select relationship_type, onboarding_done from user_profiles where user_id=?", a.uid)
        env.check(rows and rows[0][0] == "married with kids",
                  f"free-text relationship type not stored as typed: {rows}")
        env.check(rows and rows[0][1] == 1, "Alice onboarding not marked done")
        rows = env.db("select onboarding_done from user_profiles where user_id=?", b.uid)
        env.check(rows and rows[0][0] == 1, "Bob /skip did not finish onboarding")


def sc_facts_privacy(env: Env) -> None:
    pr = env.pair()
    if not pr:
        return
    a, b = pr
    a.say("#keep I get anxious when plans change last minute")
    b.say("#keep I feel guilty about working late so often")
    ra = a.say("/whatdoyouknow")
    rb = b.say("/whatdoyouknow")
    env.check("ANXIOUS" in " ".join(m["text"] for m in ra).upper(), "Alice's fact missing from her list")
    env.check("GUILTY" not in " ".join(m["text"] for m in ra).upper(), "PRIVACY: Bob's fact listed for Alice")
    env.check("ANXIOUS" not in " ".join(m["text"] for m in rb).upper(), "PRIVACY: Alice's fact listed for Bob")
    a.say("/status")
    a.say("/usage")


def sc_relay(env: Env) -> None:
    if not env.review:
        raise Skip("relay expects review_before_send=true; see relay_no_review")
    pr = env.pair()
    if not pr:
        return
    a, b = pr
    token = "RELAYTOKEN1"
    got = relay_through(env, a, b, token)
    if got and env.has_db():
        rows = env.db("select status from pending_batch where sender_id=? order by id desc limit 1", a.uid)
        env.check(rows and rows[0][0] == "sent", f"relay batch not 'sent' after delivery: {rows}")


def sc_discard(env: Env) -> None:
    pr = env.pair()
    if not pr:
        return
    a, b = pr
    r = a.say("#send DISCARDTOKEN forget it, you are useless")
    di = find_button(r, "di:")
    if not env.check(di, "no discard button on draft"):
        return
    a.click(di)
    got = b.drain(8)
    env.check(not any("DISCARDTOKEN" in m.get("text", "").upper() for m in got),
              "PRIVACY: Bob received a DISCARDED relay")
    if env.has_db():
        rows = env.db("select status from pending where sender_id=? order by id desc limit 1", a.uid)
        env.check(rows and rows[0][0] == "discarded", f"pending row not 'discarded': {rows}")


def sc_misc(env: Env) -> None:
    pr = env.pair()
    if not pr:
        return
    a, _b = pr
    for cmd in ("/history", "/export", "/goal Stop bringing up past arguments", "/goals",
                "/mood stressed", "/help"):
        a.say(cmd)


def sc_guards(env: Env) -> None:
    """DB-level assertions: the LLM cannot publish facts, no dupes, no cross-user tampering."""
    if not env.has_db():
        raise Skip("guards read the sqlite file; pass --db")
    pr = env.pair()
    if not pr:
        return
    a, b = pr
    a.say("#keep I like tea")
    a.say("#keep I like tea")  # duplicate
    rows = env.db("select visibility, text from facts where owner_id=? and text like '%TEA%'", a.uid)
    env.log(f"   DB facts matching TEA for Alice: {rows}")
    env.check(len(rows) == 1, f"dedupe: expected 1 TEA fact, found {len(rows)}")
    env.check(all(v == "private" for v, _ in rows), "PRIVACY: LLM-labelled shared fact was stored shared")
    env.check(not env.db("select 1 from facts where visibility='shared' and owner_id in (?, ?)", a.uid, b.uid),
              "PRIVACY: some fact of this couple is shared")
    fid = env.db("select id from facts where owner_id=? and text like '%TEA%'", a.uid)[0][0]
    b.say(f"/forget {fid}")
    env.check(env.db("select 1 from facts where id=?", fid), "AUTHZ: Bob deleted Alice's fact via /forget")
    b.click(f"fv:{fid}")
    env.check(env.db("select visibility from facts where id=?", fid)[0][0] == "private",
              "AUTHZ: Bob changed the visibility of Alice's fact")
    a.say("#send you are late again")
    pid = env.db("select max(id) from pending where sender_id=?", a.uid)[0][0]
    b.click(f"ap:{pid}")  # Bob tries to approve Alice's draft
    st = env.db("select status from pending where id=?", pid)[0][0]
    env.log(f"   pending {pid} status after Bob's approve attempt: {st}")
    env.check(st == "awaiting_approval", f"AUTHZ: Bob changed Alice's pending relay to {st}")


def sc_rest_session(env: Env) -> None:
    """The documented programmatic session end to end, with state assertions."""
    probe = Client(env, "probe")
    status, _ = probe.request("GET", "/healthz", auth=False)
    env.check(status == 200, f"GET /healthz -> {status}")
    status, meta = probe.request("GET", "/api/v1/meta", auth=False)
    env.check(status == 200 and isinstance(meta, dict) and meta.get("registration") == "open"
              and "transport" in meta and "llm" in meta, f"unexpected /meta: {status} {meta}")
    status, _ = probe.request("GET", "/api/v1/me", auth=False)
    env.check(status == 401, f"unauthenticated /me -> {status}, expected 401")

    pr = env.pair(names=("Asha", "Ben"))
    if not pr:
        return
    a, b = pr
    env.check(a.me().get("name") == "Asha" and b.me().get("name") == "Ben", "names not returned by /me")
    env.check(a.me().get("partner_name") == "Ben", "partner_name missing after /join")
    if env.has_db():
        env.check(env.db("select name from users where tg_id=?", a.uid) == [("Asha",)],
                  "users.name not stored for Asha")
        env.check(env.db("select name from users where tg_id=?", b.uid) == [("Ben",)],
                  "users.name not stored for Ben")
        env.check(env.db("select partner_id from users where tg_id=?", a.uid) == [(b.uid,)],
                  "partner_id not set")

    relay_through(env, a, b, "SESSIONTOKEN")

    nothing = head("mediate_no_input", window="x")[:20]
    first = " ".join(m["text"] for m in a.say("/mediate"))
    env.check(first.startswith(nothing), f"/mediate with no input should say so, got: {first[:60]!r}")

    a.say("#keep SESSIONFACT likes hiking")
    r = a.say("/whatdoyouknow")
    fv = find_button(r, "fv:")
    if not env.check(fv, "/whatdoyouknow has no share button"):
        return
    before = button_labels(r, "fv:")
    a.click(fv)
    after = button_labels(a.say("/whatdoyouknow"), "fv:")
    env.check(before != after and any("🔒" in x for x in after), f"share toggle did not flip the label: {before} -> {after}")
    if env.has_db():
        env.check(env.db("select visibility from facts where id=?", int(fv.split(":")[1])) == [("shared",)],
                  "fact not shared after fv: toggle")
    second = " ".join(m["text"] for m in a.say("/mediate"))
    env.check(second and not second.startswith(nothing), "/mediate still reports no input after sharing a fact")
    a.click(fv)  # back to private
    if env.has_db():
        env.check(env.db("select visibility from facts where id=?", int(fv.split(":")[1])) == [("private",)],
                  "fact not private after second fv: toggle")


def _call_text(call: dict) -> str:
    return call["system"] + "\n" + call["user_content"]


def sc_mediate(env: Env) -> None:
    pr = env.pair()
    if not pr:
        return
    a, b = pr
    env.fake()  # skips when the call log is unavailable
    shared = "SHAREDSENTINEL-ALICE-4410"
    a.say(f"#keep {shared} loves hiking")
    fv = only_fact_button(env, a)
    if not fv:
        return
    a.click(fv)
    a.say("#keep PRIVSENTINEL-ALICE-1182 quietly worried about money")
    b.say("#keep PRIVSENTINEL-BOB-9024 secretly planning a trip")
    b.say("RAWSENTINEL-BOB-5519 he seems distant lately [theme]")
    a.say("RAWSENTINEL-ALICE-7731 I feel ignored [selfstmt]")
    for who, other in ((a, "Bob"), (b, "Alice")):
        env.clear_calls()
        reply = who.say("/mediate")
        calls = [c for c in env.fake() if "## Request" in c["user_content"]]
        if not env.check(len(calls) == 1, f"{who.name}: expected 1 mediate prompt, saw {len(calls)}"):
            continue
        text = _call_text(calls[0])
        env.check(reply, f"{who.name}: /mediate returned nothing")
        env.check("THEME-OTHER-FEELS-UNHEARD" in text, f"{who.name}: the other partner's impression is missing")
        env.check("i feel ignored" not in text.lower(), f"{who.name}: first-person self-statement reached the prompt")
        env.check("RAWSENTINEL" not in text, f"PRIVACY: raw chat text reached the mediate prompt ({who.name})")
        env.check("PRIVSENTINEL" not in text, f"PRIVACY: a private fact reached the mediate prompt ({who.name})")
        env.check(shared in text, f"{who.name}: the shared fact is missing from the mediate prompt")


def sc_share_toggle(env: Env) -> None:
    pr = env.pair()
    if not pr:
        return
    a, b = pr
    env.fake()
    sentinel = "SHARETOGGLEFACT-3391"
    a.say(f"#keep {sentinel} enjoys long walks")
    fv = only_fact_button(env, a)
    if not fv:
        return

    def partner_sees_fact(label: str) -> bool:
        env.clear_calls()
        b.say(f"hello there {label}")
        calls = env.fake()
        env.check(calls, f"{label}: no LLM call recorded for Bob's message")
        return any(sentinel in _call_text(c) for c in calls)

    env.check(not partner_sees_fact("before"), "PRIVACY: a private fact reached the partner's prompt")
    b.click(fv)
    env.check(not partner_sees_fact("nonowner"), "AUTHZ: a non-owner toggle made Alice's fact visible")
    a.click(fv)
    env.check(partner_sees_fact("shared"), "a shared fact did not reach the partner's prompt")
    a.click(fv)
    env.check(not partner_sees_fact("private-again"), "PRIVACY: a fact made private again still reached the partner")


def sc_relay_no_review(env: Env) -> None:
    if env.review:
        raise Skip("needs a rig with review_before_send=false")
    pr = env.pair()
    if not pr:
        return
    a, b = pr
    got = relay_through(env, a, b, "NOREVIEWTOKEN")
    if got:
        extra = a.drain(4)
        env.check(not find_button(extra, "ba:"), "a review prompt was sent although review_before_send is false")
    if got and env.has_db():
        rows = env.db("select status from pending_batch where sender_id=? order by id desc limit 1", a.uid)
        env.check(rows and rows[0][0] == "sent", f"relay batch not 'sent': {rows}")


def sc_periodic(env: Env) -> None:
    """Digest, weekly check-in (with answers), nudges and backup, driven by short loop settings."""
    p = env.periodic
    if not p:
        raise Skip("needs a rig started with short loop intervals (run_all.py phase 3)")
    pr = env.pair()
    if not pr:
        return
    a, b = pr
    a.say("#keep PERIODICFACT likes tea")
    fv = only_fact_button(env, a)
    if not fv:
        return
    a.click(fv)
    if not env.check(time.time() < p["target_epoch"], "setup finished after the target minute; rerun"):
        return
    deadline = p["target_epoch"] + 40
    env.log(f"   waiting up to {deadline - time.time():.0f}s for digest/check-in at {p['time']} and nudges...")
    kinds = {
        "digest": lambda x: head("digest_header") in x,
        "checkin": lambda x: head("checkin_questions_header") in x,
        "nudge": lambda x: x.startswith("💬") or x.startswith("💛"),
    }
    seen: dict[tuple[str, str], bool] = {}
    while time.time() < deadline and len(seen) < 6:
        for c in (a, b):
            c.poll(wait=1)
            for m in c.inbox:
                for kind, test in kinds.items():
                    if test(m.get("text", "")):
                        seen[(kind, c.name)] = True
    for kind in kinds:
        for c in (a, b):
            env.check(seen.get((kind, c.name)), f"{c.name} never received the {kind} message")
    a_id, b_id = sorted((a.uid, b.uid))
    couple = f"{a_id}:{b_id}"
    rows = env.db("select config_value from couple_config where couple_id=? and config_key='last_digest'", couple)
    env.check(rows, "couple_config.last_digest was not written")
    rows = env.db("select status from checkins where couple_id=?", couple)
    env.check(rows and rows[-1][0] == "relay_queue", f"check-in row missing or not pending: {rows}")

    # answer the check-in: first answer waits, second triggers the summary for both
    waiting = a.say("Check-in answer from Alice: the week was fine")
    env.check(any(head("checkin_waiting_partner") in m["text"] for m in waiting),
              "first check-in answer was not acknowledged")
    summary_b = b.say("Check-in answer from Bob: I am grateful for tea")
    env.check(any(head("checkin_summary_header") in m["text"] for m in summary_b),
              "Bob did not get the check-in summary")
    env.check(a.wait_for(lambda m: head("checkin_summary_header") in m.get("text", ""), 10),
              "Alice did not get the check-in summary")
    rows = env.db("select status, summary from checkins where couple_id=? order by id desc limit 1", couple)
    env.check(rows and rows[0][0] == "complete" and rows[0][1], f"check-in not complete with a summary: {rows}")

    files = sorted(glob.glob(os.path.join(p["backup_dir"], "pairtner_*.db")))
    if env.check(files, f"no backup file in {p['backup_dir']}"):
        con = sqlite3.connect(f"file:{files[-1]}?mode=ro", uri=True)
        try:
            tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        finally:
            con.close()
        env.check("users" in tables, "backup file is not a copy of the database")


SCENARIOS: dict[str, Callable[[Env], None]] = {
    "pair_onboard": sc_pair_onboard,
    "facts_privacy": sc_facts_privacy,
    "relay": sc_relay,
    "discard": sc_discard,
    "misc": sc_misc,
    "guards": sc_guards,
    "rest_session": sc_rest_session,
    "mediate": sc_mediate,
    "share_toggle": sc_share_toggle,
    "relay_no_review": sc_relay_no_review,
    "periodic": sc_periodic,
}
# Scenarios that need a differently configured rig run in their own run_all phase.
PHASE1 = [n for n in SCENARIOS if n not in ("relay_no_review", "periodic")]


def run_scenarios(env: Env, names: list[str]) -> None:
    """Run *names* in order; a scenario that cannot run is skipped with a notice."""
    for name in names:
        env.current = name
        env.log(f"\n===== {name} =====")
        t0 = time.time()
        try:
            SCENARIOS[name](env)
        except Skip as exc:
            env.log(f"   (skipped: {exc})")
        except OSError as exc:  # connection refused, timeouts, DNS: the rig is not reachable
            env.flag(f"rig unreachable or too slow: {exc}")
        env.log(f"   ({time.time() - t0:.1f}s)")


def report(env: Env) -> int:
    env.log("\n===== FLAGS =====")
    for f in env.flags or ["none"]:
        env.log(f"  - {f}")
    return 1 if env.flags else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=None, help=f"rig base URL (default {DEFAULT_URL})")
    ap.add_argument("--db", default=None, help="sqlite file of the rig (default dev/localtest/test.db without --url)")
    ap.add_argument("--fake-url", default=DEFAULT_FAKE_URL, help="fake LLM base URL for /_calls")
    ap.add_argument("--no-review", action="store_true", help="the rig runs with review_before_send=false")
    ap.add_argument("names", nargs="*", metavar="name", help="scenarios to run: " + ", ".join(SCENARIOS))
    args = ap.parse_args(argv)
    unknown = [n for n in args.names if n not in SCENARIOS]
    if unknown:
        ap.error(f"unknown scenario(s) {unknown}; choose from {list(SCENARIOS)}")
    url = args.url or DEFAULT_URL
    db = args.db or (str(HERE / "test.db") if not args.url else None)
    env = Env(url, db_path=db, fake_url=args.fake_url, review=not args.no_review)
    names = args.names or PHASE1
    run_scenarios(env, names)
    code = report(env)
    (HERE / f"transcript-{'-'.join(names) if args.names else 'all'}.log").write_text("\n".join(env.lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
