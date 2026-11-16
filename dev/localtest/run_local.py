"""Local test rig: pAIrtner + local Ollama + WebSocket adapter + static chat UI.

Never starts Telegram (a second poller on the real token would break the live bot).
Uses a throwaway DB. Run from anywhere:

    uv run python dev/localtest/run_local.py [--keep] [--ws-port 8765] [--http-port 8766]

Open  http://localhost:8766/duo.html  for the two-pane view.
"""
from __future__ import annotations

import argparse
import asyncio
import functools
import http.server
import json
import logging
import os
import pathlib
import secrets
import threading

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
os.chdir(ROOT)
import sys
sys.path.insert(0, str(ROOT))

ap = argparse.ArgumentParser()
ap.add_argument("--keep", action="store_true", help="keep the existing test DB")
ap.add_argument("--ws-port", type=int, default=8765)
ap.add_argument("--http-port", type=int, default=8766)
ap.add_argument("--llm", choices=["ollama", "fake"], default="ollama",
                help="ollama = local qwen3; fake = deterministic UPPER(input) endpoint")
ap.add_argument("--cooldown", type=int, default=1, help="relay cool-down minutes")
args = ap.parse_args()

DB = HERE / "test.db"
if not args.keep and DB.exists():
    DB.unlink()

LLM_URL = "http://localhost:11434/v1"
MODEL = os.environ.get("LOCAL_MODEL", "qwen3:8b")
if args.llm == "fake":
    sys.path.insert(0, str(HERE))
    import fake_llm
    fake_llm.start(11435)
    LLM_URL, MODEL = "http://127.0.0.1:11435/v1", "fake-upper"

# Force a fully local, isolated configuration (explicit env wins over any .env file).
os.environ.update({
    "TELEGRAM_BOT_TOKEN": "local-test-not-used",
    "GEMINI_API_KEY": "",
    "LLM_PROVIDER": "openai_compat",
    "LLM_BASE_URL": LLM_URL,
    "LLM_API_KEY": "ollama",
    "LLM_MODEL_DEFAULT": MODEL,
    "LLM_MODEL_HEAVY": MODEL,
    "LLM_REASONING_EFFORT": "none",
    "DB_PATH": str(DB),
    "WS_PORT": str(args.ws_port),
    "COOLDOWN_MINUTES": str(args.cooldown),
    "ALLOWED_USER_IDS": "",
})

from couples_bot.adapters.websocket_adapter import WebSocketAdapter  # noqa: E402
from couples_bot.config import load_config  # noqa: E402
from couples_bot.db import create_auth_token, init_db  # noqa: E402
from couples_bot.handlers import BotService  # noqa: E402
from couples_bot.llm import create_llm_client  # noqa: E402
from couples_bot.prompts import load_persona  # noqa: E402

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", level=logging.INFO)
for noisy in ("httpx", "httpcore", "openai"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

USERS = {1001: "Alice", 1002: "Bob"}


async def main() -> None:
    config = load_config()
    load_persona(os.path.join(config.prompts_dir, "persona.md"))
    await init_db(config.db_path)
    tokens = {}
    for uid in USERS:
        tokens[uid] = secrets.token_urlsafe(16)
        await create_auth_token(config.db_path, uid, tokens[uid])
    (HERE / "web" / "tokens.json").write_text(json.dumps(
        {"ws_port": args.ws_port, "users": {str(u): {"name": n, "token": tokens[u]} for u, n in USERS.items()}},
        indent=2,
    ))

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(HERE / "web"))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", args.http_port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    logging.info("UI: http://localhost:%d/duo.html   WS: ws://localhost:%d", args.http_port, args.ws_port)

    llm = create_llm_client(config, config.db_path)
    service = BotService(config, config.db_path, llm)
    await WebSocketAdapter(service, config).start()


if __name__ == "__main__":
    asyncio.run(main())
