"""Shared construction of the local test rig (used by run_local.py and run_all.py).

The rig is the real BotService behind the real RestAdapter, with a throwaway
database and an OpenAI-compatible local LLM (Ollama or fake_llm.py). Config is
built directly (no .env, no settings.yaml), so no secret is needed and Telegram
is never started.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from couples_bot import pack  # noqa: E402
from couples_bot.__main__ import _set_privacy_info  # noqa: E402
from couples_bot.adapters.rest_adapter import RestAdapter  # noqa: E402
from couples_bot.config import Config, load_config  # noqa: E402
from couples_bot.db import init_db  # noqa: E402
from couples_bot.handlers import BotService  # noqa: E402
from couples_bot.llm import create_llm_client  # noqa: E402

from couples_bot.rest.throttle import SlidingWindowLimiter  # noqa: E402


def make_config(
    db_path: str,
    *,
    llm_url: str,
    model: str,
    reasoning: str = "",
    port: int = 0,
    cooldown: int = 1,
    review_before_send: bool = True,
    delivery_interval: int = 1,
    prompt_pack: str = str(ROOT / "packs" / "default"),
    loops: bool = False,
    **overrides,
) -> Config:
    """Rig Config: local LLM, open REST registration on 127.0.0.1, no Telegram.

    ``loops=False`` disables digest, check-in, nudges and backup so they cannot
    fire during interactive use; the periodic scenario turns them on.
    """
    cfg = Config(
        telegram_token="",
        db_path=db_path,
        llm_provider="openai_compat",
        llm_base_url=llm_url,
        llm_api_key="local",
        llm_model_default=model,
        llm_model_heavy=[model],
        llm_reasoning_effort=reasoning,
        cooldown_minutes=cooldown,
        review_before_send=review_before_send,
        delivery_check_interval_seconds=delivery_interval,
        rate_limit_per_minute=100000,
        rate_limit_per_day=100000,
        heavy_daily_cap=100000,
        prompt_pack=prompt_pack,
        adapter="rest",
        rest_host="127.0.0.1",
        rest_port=port,
        rest_registration="open",
        rest_long_poll_max_seconds=5,
        digest_enabled=loops,
        checkin_enabled=loops,
        nudges_enabled=loops,
        appreciation_enabled=loops,
        backup_enabled=loops,
    )
    return dataclasses.replace(cfg, **overrides) if overrides else cfg


DUMMY_TELEGRAM_TOKEN = "dummy-token-never-used"


def find_env_file() -> pathlib.Path | None:
    """The main checkout's ``.env`` (a worktree has none), or None if absent."""
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    candidate = pathlib.Path(out).parent / ".env"
    return candidate if candidate.is_file() else None


def make_gemini_config(
    db_path: str,
    *,
    port: int = 0,
    cooldown: int = 1,
    review_before_send: bool = True,
    delivery_interval: int = 1,
    prompt_pack: str = str(ROOT / "packs" / "default"),
    loops: bool = False,
    **overrides,
) -> Config:
    """Rig Config for the real Gemini provider (key read from ``.env`` / env).

    The Telegram token is forced to a dummy value and the adapter to ``rest``,
    so Telegram can never start.  The API key is only ever read by
    ``load_config``; it is never printed or logged here.
    """
    os.environ["TELEGRAM_BOT_TOKEN"] = DUMMY_TELEGRAM_TOKEN
    os.environ["ADAPTER"] = "rest"
    env_file = find_env_file()
    if env_file is not None:
        from dotenv import load_dotenv
        load_dotenv(env_file)  # does not override variables already set
    os.environ["TELEGRAM_BOT_TOKEN"] = DUMMY_TELEGRAM_TOKEN
    cfg = load_config(str(ROOT / "settings.yaml"))
    cfg = dataclasses.replace(
        cfg,
        telegram_token=DUMMY_TELEGRAM_TOKEN,
        db_path=db_path,
        allowed_user_ids=frozenset(),
        cooldown_minutes=cooldown,
        review_before_send=review_before_send,
        delivery_check_interval_seconds=delivery_interval,
        rate_limit_per_minute=100000,
        rate_limit_per_day=100000,
        heavy_daily_cap=100000,
        prompt_pack=prompt_pack,
        adapter="rest",
        rest_host="127.0.0.1",
        rest_port=port,
        rest_registration="open",
        rest_long_poll_max_seconds=5,
        digest_enabled=loops,
        checkin_enabled=loops,
        nudges_enabled=loops,
        appreciation_enabled=loops,
        backup_enabled=loops,
    )
    assert cfg.adapter == "rest", "rig must never run the Telegram adapter"
    return dataclasses.replace(cfg, **overrides) if overrides else cfg


async def start_rig(
    config: Config, llm=None,
) -> tuple[BotService, RestAdapter, asyncio.Task]:
    """Initialise the DB, build service and adapter, and wait until listening.

    ``llm`` is an optional pre-built client (e.g. wrapped for tracing).
    """
    pack.activate(config.prompt_pack)
    await init_db(config.db_path)
    if llm is None:
        llm = create_llm_client(config, config.db_path)
    service = BotService(config, config.db_path, llm)
    adapter = RestAdapter(service, config)
    # The rig registers many throwaway users from one address.
    adapter.api.register_limiter = SlidingWindowLimiter(100000, 60.0)
    _set_privacy_info(service, config, [adapter])
    task = asyncio.create_task(adapter.start())
    ready = asyncio.create_task(adapter.ready.wait())
    done, _ = await asyncio.wait({task, ready}, return_when=asyncio.FIRST_COMPLETED)
    if task in done:
        ready.cancel()
        task.result()  # re-raise the startup error
    return service, adapter, task


async def stop_rig(adapter: RestAdapter, task: asyncio.Task) -> None:
    """Stop the adapter (and its background loops) and wait for start() to return."""
    await adapter.stop()
    await task
