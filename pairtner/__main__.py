"""Entry point — thin launcher that picks an adapter and starts it."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys


from pairtner import pack
from pairtner.adapters import get_adapter
from pairtner.adapters.cli_adapter import CLIAdapter
from pairtner.config import load_config
from pairtner.db import init_db
from pairtner.handlers import BotService
from pairtner.handlers.service import PrivacyInfo
from pairtner.llm import create_llm_client, describe_llm


logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
# Quiet the noisy HTTP polling logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram.ext.Updater").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="pAIrtner couples bot")
    parser.add_argument(
        "--adapter",
        choices=["telegram", "websocket", "cli", "both", "rest", "telegram+rest"],
        default=None,
        help="Transport adapter (default: from config)",
    )
    parser.add_argument(
        "--port", type=int, default=None,
        help="WebSocket port (overrides config)",
    )
    parser.add_argument(
        "--user-id", type=int, default=None,
        help="Single-user mode for CLI adapter",
    )
    args = parser.parse_args()

    config = load_config()
    from pairtner.core.prompts import set_bot_name
    set_bot_name(config.bot_name)
    adapter_name = args.adapter or config.adapter
    logger.info(
        "Config loaded — provider: %s, default model: %s",
        config.llm_provider, config.model_default,
    )

    # Load and validate the prompt pack before touching the database.
    try:
        active_pack = pack.activate(config.prompt_pack)
    except pack.PackError as exc:
        logger.error("%s", exc)
        sys.exit(2)
    snapshot = active_pack.snapshot()
    logger.info(
        "Prompt pack loaded: %s@%s pack_hash=%s",
        snapshot["name"], snapshot["version"], snapshot["pack_hash"][:12],
    )

    # Init DB synchronously before the event loop starts
    asyncio.run(init_db(config.db_path))
    logger.info("Database initialized at %s", config.db_path)

    # Override ws_port if --port given
    if args.port is not None:
        # Frozen dataclass — use object.__setattr__
        object.__setattr__(config, "ws_port", args.port)

    llm = create_llm_client(config, config.db_path)
    service = BotService(config, config.db_path, llm)

    if adapter_name == "both":
        # Run Telegram + WebSocket concurrently
        asyncio.run(_run_both(service, config))
    elif adapter_name == "telegram+rest":
        asyncio.run(_run_telegram_rest(service, config))
    elif adapter_name == "cli":
        adapter = CLIAdapter(service, config, user_id=args.user_id)
        asyncio.run(adapter.start())
    else:
        adapter_cls = get_adapter(adapter_name)
        adapter = adapter_cls(service, config)
        _set_privacy_info(service, config, [adapter])
        asyncio.run(adapter.start())


def _set_privacy_info(service: BotService, config, adapters: list) -> None:
    """Tell the service where messages travel, for the /start and /help note."""
    transport = " and ".join(a.transport_description for a in adapters)
    service.privacy_info = PrivacyInfo(transport=transport, llm=describe_llm(config))


async def _run_telegram_rest(service: BotService, config) -> None:
    """Run Telegram and REST together with one shared set of background loops."""
    from pairtner.adapters.multi import MultiAdapter
    from pairtner.adapters.rest_adapter import RestAdapter
    from pairtner.adapters.telegram_adapter import TelegramAdapter

    tg = TelegramAdapter(service, config, run_background=False)
    rest = RestAdapter(service, config, run_background=False)
    multi = MultiAdapter([tg, rest])
    _set_privacy_info(service, config, [tg, rest])
    await multi.start()


async def _run_both(service: BotService, config) -> None:
    """Run Telegram and WebSocket adapters concurrently."""
    from pairtner.adapters.telegram_adapter import TelegramAdapter
    from pairtner.adapters.websocket_adapter import WebSocketAdapter

    tg = TelegramAdapter(service, config)
    ws = WebSocketAdapter(service, config)

    await asyncio.gather(tg.start(), ws.start())


if __name__ == "__main__":
    main()
