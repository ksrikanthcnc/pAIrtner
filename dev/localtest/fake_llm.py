"""Deterministic OpenAI-compatible fake LLM for plumbing tests (no model, no latency).

reply       = UPPER(the user's message)
relay_draft = UPPER(message) when the prompt is a relay/#send prompt, else null
new_facts   = [UPPER(message) labelled "shared"] for #keep prompts. A deliberately
              misbehaving model, so the "LLM may never publish a fact" guard is exercised.

    uv run python tmp-kiro/localtest/fake_llm.py [--port 11435]
"""
from __future__ import annotations

import argparse
import http.server
import json
import re
import threading

_MSG = re.compile(r"\n## (Current message|Message to relay to partner|Message to save)[^\n]*\n(.*)\Z", re.S)


def make_reply(user_content: str) -> dict:
    m = _MSG.search(user_content)
    kind, msg = (m.group(1), m.group(2).strip()) if m else ("Current message", user_content.strip().splitlines()[-1])
    up = msg.upper()
    return {
        "reply": up,
        "new_facts": [{"text": up, "visibility": "shared", "category": "other", "fact_type": "stable"}]
        if kind == "Message to save" else [],
        "relay_draft": up if kind == "Message to relay to partner" else None,
        "detected_mood": None,
        "patterns_observed": [],
        "partner_themes": [],
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
        if self.path.rstrip("/").endswith("/models"):
            self._send({"object": "list", "data": [{"id": "fake-upper", "object": "model"}]})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        if not self.path.endswith("/chat/completions"):
            return self._send({"error": "not found"}, 404)
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        user = next((m["content"] for m in reversed(req["messages"]) if m["role"] == "user"), "")
        self._send({
            "id": "fake", "object": "chat.completion", "model": req.get("model", "fake-upper"),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": json.dumps(make_reply(user))}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })


def start(port: int = 11435) -> http.server.ThreadingHTTPServer:
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
