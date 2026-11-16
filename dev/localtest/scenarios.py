"""Scripted two-user scenarios against the local rig (run_local.py must be running).

    uv run --no-project --with websockets python tmp-kiro/localtest/scenarios.py [name ...]

Writes a transcript to tmp-kiro/localtest/transcript-<scenario>.log and prints FLAGS for anomalies.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import re
import sys
import time

import websockets

HERE = pathlib.Path(__file__).resolve().parent
CFG = json.loads((HERE / "web" / "tokens.json").read_text())
URL = f"ws://localhost:{CFG['ws_port']}"
SUSPECT = [r'"reply"\s*:', r"trouble connecting", r"Traceback", r"new_facts", r"relay_draft", r"\bNone\b"]


class Client:
    def __init__(self, uid: str, name: str, token: str, log):
        self.uid, self.name, self.token, self.log = uid, name, token, log
        self.ws = None
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.flags: list[str] = []

    async def connect(self):
        self.ws = await websockets.connect(URL)
        await self.ws.send(json.dumps({"type": "auth", "user_id": self.uid, "token": self.token}))
        assert json.loads(await self.ws.recv())["type"] == "auth_ok"
        asyncio.create_task(self._pump())

    async def _pump(self):
        async for raw in self.ws:
            await self.inbox.put(json.loads(raw))

    async def say(self, text: str, idle: float = 6.0, first: float = 150.0):
        self.log(f"{self.name} > {text}")
        await self.ws.send(json.dumps({"type": "message", "user_id": self.uid, "text": text}))
        return await self.collect(idle, first)

    async def click(self, data: str, idle: float = 6.0, first: float = 150.0):
        self.log(f"{self.name} [click {data}]")
        await self.ws.send(json.dumps({"type": "callback", "user_id": self.uid, "callback_data": data}))
        return await self.collect(idle, first)

    async def collect(self, idle: float = 6.0, first: float = 150.0):
        out, timeout, t0 = [], first, time.time()
        while True:
            try:
                m = await asyncio.wait_for(self.inbox.get(), timeout)
            except asyncio.TimeoutError:
                break
            if m["type"] == "typing":
                continue
            out.append(m)
            timeout = idle
            self._show(m)
        if not out:
            self.flags.append(f"{self.name}: no reply within {first:.0f}s")
        self.log(f"   ({time.time() - t0:.1f}s)")
        return out

    def _show(self, m):
        tag = {"reply": "bot", "relay": "RELAY"}.get(m["type"], m["type"])
        btns = ""
        if m.get("buttons"):
            btns = "  [" + " | ".join(f"{b['text']}={b['callback_data']}" for r in m["buttons"] for b in r) + "]"
        self.log(f"   {self.name} < {tag}: {m.get('text', '')}{btns}")
        for pat in SUSPECT:
            if re.search(pat, m.get("text", "") or ""):
                self.flags.append(f"{self.name}: suspicious /{pat}/ in: {m.get('text','')[:80]!r}")
        if m["type"] == "error":
            self.flags.append(f"{self.name}: error {m.get('text')}")

    async def drain(self, seconds: float):
        """Collect anything that arrives (e.g. delayed relays) for a fixed window."""
        end = time.time() + seconds
        out = []
        while time.time() < end:
            try:
                m = await asyncio.wait_for(self.inbox.get(), max(0.1, end - time.time()))
            except asyncio.TimeoutError:
                break
            if m["type"] != "typing":
                out.append(m)
                self._show(m)
        return out


def button(msgs, label_part: str):
    for m in msgs:
        for row in m.get("buttons") or []:
            for b in row:
                if label_part.lower() in b["text"].lower():
                    return b["callback_data"]
    return None


async def scenario_pair_onboard(a: Client, b: Client, log):
    r = await a.say("/start")
    code = next((w for m in r for w in re.findall(r"\b[A-Z0-9]{6}\b", m.get("text", ""))), None)
    log(f"   -> pairing code: {code}")
    if not code:
        a.flags.append("no pairing code found in /start reply")
        return
    await b.say("/start")
    await b.say(f"/join {code}")
    await a.collect(idle=6, first=30)  # A is notified + gets onboarding Q1
    await a.say("married with kids")
    await a.say("6")
    await a.say("yes")
    await a.say("30s")
    await a.say("her sense of humour and patience")
    await a.say("I wish we had more time for just us")
    await b.say("/skip")


async def scenario_facts_privacy(a: Client, b: Client, log):
    await a.say("#keep I get anxious when plans change last minute")
    await b.say("#keep I feel guilty about working late so often")
    await a.say("/whatdoyouknow")
    await b.say("/whatdoyouknow")
    await a.say("/status")
    await a.say("/usage")


async def scenario_relay(a: Client, b: Client, log):
    r = await a.say("#send you never help with the dishes and I'm sick of it")
    cb = button(r, "approve")
    if not cb:
        a.flags.append("no approve button on #send draft")
        return
    await a.click(cb)
    log("   waiting for cool-down, then the sender's review prompt (up to 150s)...")
    review = await a.collect(idle=3, first=150)
    send_all = button(review, "send all")
    if not send_all:
        a.flags.append("no 'Send All' review prompt arrived after cool-down")
        return
    await a.click(send_all)
    got = await b.drain(20)
    if not any(m["type"] == "relay" for m in got):
        b.flags.append("Bob never received the approved relay after Send All")


async def scenario_discard(a: Client, b: Client, log):
    r = await a.say("#send forget it, you are useless")
    cb = button(r, "discard")
    if not cb:
        a.flags.append("no discard button on draft")
        return
    await a.click(cb)
    got = await b.drain(70)  # longer than the cool-down
    if any(m["type"] == "relay" for m in got):
        b.flags.append("PRIVACY: Bob received a DISCARDED relay")


def _db(sql: str, *params):
    import sqlite3
    con = sqlite3.connect(HERE / "test.db")
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


async def scenario_guards(a: Client, b: Client, log):
    """DB-level assertions: LLM can't publish facts, no dupes, no cross-user tampering."""
    await a.say("#keep I like tea")
    await a.say("#keep I like tea")           # duplicate
    rows = _db("select visibility, text from facts where owner_id=1001 and text like '%TEA%'")
    log(f"   DB facts matching TEA for Alice: {rows}")
    if len(rows) != 1:
        a.flags.append(f"dedupe: expected 1 TEA fact, found {len(rows)}")
    if any(v != "private" for v, _ in rows):
        a.flags.append("PRIVACY: LLM-labelled shared fact was stored shared")
    if _db("select 1 from facts where visibility='shared'"):
        a.flags.append("PRIVACY: some fact in the DB is shared")

    fid = _db("select id from facts where owner_id=1001 and text like '%TEA%'")[0][0]
    await b.say(f"/forget {fid}")
    if not _db("select 1 from facts where id=?", fid):
        b.flags.append("AUTHZ: Bob deleted Alice's fact via /forget")

    r = await a.say("#send you are late again")
    pid = _db("select max(id) from pending where sender_id=1001")[0][0]
    await b.click(f"ap:{pid}")                # Bob tries to approve Alice's draft
    st = _db("select status from pending where id=?", pid)[0][0]
    log(f"   pending {pid} status after Bob's approve attempt: {st}")
    if st != "awaiting_approval":
        b.flags.append(f"AUTHZ: Bob changed Alice's pending relay to {st}")


async def scenario_misc(a: Client, b: Client, log):
    await a.say("/history")
    await a.say("/export")
    await a.say("/goal Stop bringing up past arguments")
    await a.say("/goals")
    await a.say("/mood stressed")
    await a.say("/help")


SCENARIOS = {
    "pair_onboard": scenario_pair_onboard,
    "facts_privacy": scenario_facts_privacy,
    "relay": scenario_relay,
    "discard": scenario_discard,
    "misc": scenario_misc,
    "guards": scenario_guards,
}


async def main(names: list[str]):
    names = names or list(SCENARIOS)
    users = CFG["users"]
    all_flags = []
    lines: list[str] = []

    def log(s):
        print(s, flush=True)
        lines.append(s)

    a = Client("1001", users["1001"]["name"], users["1001"]["token"], log)
    b = Client("1002", users["1002"]["name"], users["1002"]["token"], log)
    await a.connect()
    await b.connect()
    for n in names:
        log(f"\n===== {n} =====")
        await SCENARIOS[n](a, b, log)
    all_flags = a.flags + b.flags
    log("\n===== FLAGS =====")
    for f in all_flags or ["none"]:
        log(f"  - {f}")
    (HERE / f"transcript-{'-'.join(names)}.log").write_text("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
