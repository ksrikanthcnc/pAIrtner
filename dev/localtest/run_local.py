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


def _detect_providers() -> str:
    """Auto-detect which LLM providers are available.

    Probes Ollama (localhost:11434), checks GEMINI_API_KEY env var.
    Returns the best default: 'ollama' if reachable, 'gemini' if key set, else error.
    """
    import urllib.request
    ollama_ok = False
    try:
        req = urllib.request.urlopen("http://localhost:11434/v1/models", timeout=2)
        req.close()
        ollama_ok = True
    except Exception:
        pass

    gemini_key = os.environ.get("GEMINI_API_KEY", "")

    if ollama_ok and gemini_key:
        print("🔍 Auto-detected: Ollama ✅ + Gemini ✅ (multi-provider, /model to switch)", flush=True)
        return "ollama"  # default to ollama, gemini available via /model prefix
    elif ollama_ok:
        print("🔍 Auto-detected: Ollama ✅ (set GEMINI_API_KEY for Gemini too)", flush=True)
        return "ollama"
    elif gemini_key:
        print("🔍 Auto-detected: Gemini ✅ (start Ollama for local models too)", flush=True)
        return "gemini"
    else:
        print("❌ No LLM provider found!", flush=True)
        print("   • Start Ollama: ollama serve", flush=True)
        print("   • Or set GEMINI_API_KEY=your-key", flush=True)
        raise SystemExit(1)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--keep", action="store_true", help="keep the existing test DB")
    ap.add_argument("--port", type=int, default=8766, help="REST/UI port")
    ap.add_argument("--lan", action="store_true", help="bind to 0.0.0.0 (accessible from LAN/Android)")
    ap.add_argument("--llm", choices=["auto", "ollama", "fake", "gemini"], default="auto",
                    help="auto = detect available providers; ollama = local; "
                         "fake = deterministic UPPER(input); gemini = Google Gemini")
    ap.add_argument("--fake-port", type=int, default=FAKE_PORT, help="port of the fake LLM (--llm fake)")
    ap.add_argument("--cooldown", type=int, default=1, help="relay cool-down minutes")
    ap.add_argument("--pack", default="default",
                    help="prompt pack name (default, simple, or path to pack dir)")
    return ap.parse_args(argv)


async def main(args: argparse.Namespace) -> None:
    db = HERE / "test.db"
    if not args.keep and db.exists():
        db.unlink()

    # Resolve pack path
    pack_path = args.pack
    if not os.path.isabs(pack_path) and not os.path.exists(pack_path):
        # Try as a pack name in the packs/ directory
        candidate = HERE.parent.parent / "packs" / pack_path
        if candidate.is_dir():
            pack_path = str(candidate)

    llm_mode = args.llm
    if llm_mode == "auto":
        llm_mode = _detect_providers()

    if llm_mode == "gemini":
        config = rig.make_gemini_config(
            str(db), port=args.port, cooldown=args.cooldown,
            rest_host="0.0.0.0" if args.lan else "127.0.0.1",
            prompt_pack=pack_path,
        )
    elif llm_mode == "fake":
        import fake_llm
        fake_llm.start(args.fake_port)
        llm_url, model, reasoning = f"http://127.0.0.1:{args.fake_port}/v1", "fake-upper", ""
        config = rig.make_config(
            str(db), llm_url=llm_url, model=model, reasoning=reasoning,
            port=args.port, cooldown=args.cooldown,
            rest_host="0.0.0.0" if args.lan else "127.0.0.1",
            prompt_pack=pack_path,
        )
    else:
        # ollama (default primary)
        llm_url = "http://localhost:11434/v1"
        model = os.environ.get("LOCAL_MODEL", "qwen3:8b")
        reasoning = "none"
        config = rig.make_config(
            str(db), llm_url=llm_url, model=model, reasoning=reasoning,
            port=args.port, cooldown=args.cooldown,
            rest_host="0.0.0.0" if args.lan else "127.0.0.1",
            prompt_pack=pack_path,
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
