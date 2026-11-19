"""Local test rig: pAIrtner + local LLM + the REST adapter serving the product UI.

Never starts Telegram (a second poller on the real token would break the live
bot), needs no token or .env, and uses a throwaway DB. Run from anywhere:

    uv run python dev/localtest/run_local.py [--llm ollama|fake|gemini] [--port 8766] [--keep] [--cooldown 1] [--fake-port 11435]

Open  http://127.0.0.1:8766/       (single chat; ?profile=alice&name=Alice auto-registers)
      http://127.0.0.1:8766/duo.html  (two panes, one per partner)
Scripted scenarios against it:
    uv run python dev/localtest/scenarios.py --url http://127.0.0.1:8766 rest_session
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rig  # noqa: E402
from pairtner.llm import describe_llm  # noqa: E402

FAKE_PORT = 11435


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--keep", action="store_true", help="keep the existing test DB")
    ap.add_argument("--port", type=int, default=8766, help="REST/UI port")
    ap.add_argument("--lan", action="store_true", help="bind to 0.0.0.0 (accessible from LAN/Android)")
    ap.add_argument("--llm", choices=["ollama", "fake", "gemini"], default="ollama",
                    help="ollama = local qwen3; fake = deterministic UPPER(input) endpoint; "
                         "gemini = real Google Gemini (key from the main checkout .env)")
    ap.add_argument("--fake-port", type=int, default=FAKE_PORT, help="port of the fake LLM (--llm fake)")
    ap.add_argument("--cooldown", type=int, default=1, help="relay cool-down minutes")
    return ap.parse_args(argv)


async def main(args: argparse.Namespace) -> None:
    db = HERE / "test.db"
    if not args.keep and db.exists():
        db.unlink()
    if args.llm == "gemini":
        config = rig.make_gemini_config(
            str(db), port=args.port, cooldown=args.cooldown,
            rest_host="0.0.0.0" if args.lan else "127.0.0.1",
        )
    elif args.llm == "fake":
        import fake_llm
        fake_llm.start(args.fake_port)
        llm_url, model, reasoning = f"http://127.0.0.1:{args.fake_port}/v1", "fake-upper", ""
    else:
        llm_url = "http://localhost:11434/v1"
        model = os.environ.get("LOCAL_MODEL", "qwen3:8b")
        reasoning = "none"
    if args.llm != "gemini":
        config = rig.make_config(
            str(db), llm_url=llm_url, model=model, reasoning=reasoning,
            port=args.port, cooldown=args.cooldown,
            rest_host="0.0.0.0" if args.lan else "127.0.0.1",
        )
    _service, adapter, task = await rig.start_rig(config)
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    base = f"http://{host}:{adapter.bound_port}"
    print(f"UI:      {base}/", flush=True)
    print(f"Duo UI:  {base}/duo.html", flush=True)
    if args.lan:
        import socket
        lan_ip = socket.gethostbyname(socket.gethostname())
        print(f"LAN:     http://{lan_ip}:{adapter.bound_port}/duo.html  (from Android/phone)", flush=True)
    print(f"API:     {base}/api/v1/meta   (LLM: {describe_llm(config)}, DB: {db})", flush=True)
    await task


if __name__ == "__main__":
    logging.basicConfig(format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", level=logging.INFO)
    for noisy in ("httpx", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        asyncio.run(main(parse_args()))
    except KeyboardInterrupt:
        pass
