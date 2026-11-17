"""Deterministic OpenAI-compatible fake LLM for plumbing tests (no model, no latency).

reply       = UPPER(the user's message)
relay_draft = UPPER(message) when the prompt is a relay/#send prompt, else null
new_facts   = [UPPER(message) labelled "shared"] for #keep prompts. A deliberately
              misbehaving model, so the "LLM may never publish a fact" guard is exercised.
partner_themes = fixed strings chosen by markers in the message: "[theme]" ->
              ["THEME-OTHER-FEELS-UNHEARD"], "[selfstmt]" -> ["I FEEL IGNORED"] (a weak
              model's misattributed self-statement). Fixed, not UPPER(message), so
              raw-text assertions on the mediate prompt stay meaningful.

Every chat request is recorded: GET /_calls lists {system, user_content},
DELETE /_calls clears the log.

    uv run python dev/localtest/fake_llm.py [--port 11435]
"""
from __future__ import annotations

import argparse
import http.server
import json
import re
import threading

THEME_MARKER = "[theme]"
THEME_VALUE = "THEME-OTHER-FEELS-UNHEARD"
SELFSTMT_MARKER = "[selfstmt]"
SELFSTMT_VALUE = "I FEEL IGNORED"

_CALLS: list[dict] = []
_CALLS_LOCK = threading.Lock()

_MSG = re.compile(r"\n## (Current message|Message to relay to partner|Message to save)[^\n]*\n(.*)\Z", re.S)


def make_reply(user_content: str) -> dict:
    """Build the structured reply for one prompt (pure, no I/O)."""
    m = _MSG.search(user_content)
    kind, msg = (m.group(1), m.group(2).strip()) if m else ("Current message", user_content.strip().splitlines()[-1])
    up = msg.upper()
    low = msg.lower()
    themes: list[str] = []
    if THEME_MARKER in low:
        themes.append(THEME_VALUE)
    if SELFSTMT_MARKER in low:
        themes.append(SELFSTMT_VALUE)
    return {
        "reply": up,
        "memory_ops": [{"op": "add", "kind": "working", "text": up}]
        if kind == "Message to save" else
        [{"op": "add", "kind": "working", "text": f"USER SAID: {up}"}]
        if kind == "Current message" and len(msg) > 10 else [],
        "new_facts": [{"text": up, "visibility": "shared", "category": "other", "fact_type": "stable"}]
        if kind == "Message to save" else [],
        "relay_draft": up if kind == "Message to relay to partner" else None,
        "detected_mood": "stressed" if any(w in low for w in ("stressed", "angry", "upset", "frustrated")) else None,
        "patterns_observed": [],
        "partner_themes": themes,
        "articulation_note": None,
        "horsemen_detected": [],
    }


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.rstrip("/")
        if path == "/_calls":
            with _CALLS_LOCK:
                self._send({"calls": list(_CALLS)})
        elif path.endswith("/models"):
            self._send({"object": "list", "data": [{"id": "fake-upper", "object": "model"}]})
        else:
            self._send({"error": "not found"}, 404)

    def do_DELETE(self):
        if self.path.rstrip("/") != "/_calls":
            return self._send({"error": "not found"}, 404)
        with _CALLS_LOCK:
            _CALLS.clear()
        self._send({"calls": []})

    def do_POST(self):
        if not self.path.endswith("/chat/completions"):
            return self._send({"error": "not found"}, 404)
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        user = next((m["content"] for m in reversed(req["messages"]) if m["role"] == "user"), "")
        system = "\n".join(m["content"] for m in req["messages"] if m["role"] == "system")
        with _CALLS_LOCK:
            _CALLS.append({"system": system, "user_content": user})
        self._send({
            "id": "fake", "object": "chat.completion", "model": req.get("model", "fake-upper"),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": json.dumps(make_reply(user))}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })


def start(port: int = 11435) -> http.server.ThreadingHTTPServer:
    """Start the fake in a daemon thread; ``port=0`` picks a free port."""
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=11435)
    a = ap.parse_args()
    start(a.port)
    print(f"fake LLM on http://127.0.0.1:{a.port}/v1")
    threading.Event().wait()
