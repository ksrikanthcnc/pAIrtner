"""CLI adapter — terminal-based two-user interface for local testing."""

from __future__ import annotations

import asyncio
import logging
import sys

from couples_bot import db as db_mod

from .base import BaseAdapter

logger = logging.getLogger(__name__)

# Default hardcoded user IDs for two-user mode
_USER_A_ID = 1
_USER_B_ID = 2


class CLIAdapter(BaseAdapter):
    """Adapter that provides a terminal REPL for testing without Telegram.

    In two-user mode, alternates between 'User A>' and 'User B>' prompts.
    In single-user mode (``user_id`` provided), uses a single prompt.
    """

    transport_description = "this server's terminal (no third-party messaging service)"

    def __init__(self, service, config, user_id: int | None = None) -> None:
        super().__init__(service, config)
        self._single_user_id = user_id
        self._running = False

    async def start(self) -> None:
        self._running = True

        if self._single_user_id:
            await self._ensure_user(self._single_user_id, f"User{self._single_user_id}")
            await self._run_single_user()
        else:
            # Two-user mode — ensure both users exist
            await self._ensure_user(_USER_A_ID, "UserA")
            await self._ensure_user(_USER_B_ID, "UserB")
            await self._run_two_user()

    async def stop(self) -> None:
        self._running = False

    async def send_message(
        self,
        user_id: int | str,
        text: str,
        buttons: list[list[tuple[str, str]]] | None = None,
        document: tuple[str, str] | None = None,
    ) -> None:
        prefix = f"[Bot → {user_id}]"
        print(f"\n{prefix} {text}")
        if buttons:
            for row in buttons:
                labels = " | ".join(f"[{btn_text}]" for btn_text, _ in row)
                print(f"  {labels}")
        if document:
            print(f"  📎 {document[0]}")
        sys.stdout.flush()

    async def send_typing(self, user_id: int | str) -> None:
        print(f"[Bot → {user_id}] typing...", flush=True)

    # ── internal ─────────────────────────────────────────────────────────

    async def _ensure_user(self, user_id: int, name: str) -> None:
        """Ensure a user exists in the DB for CLI testing."""
        await db_mod.ensure_user(self.service.db_path, user_id, name)

    async def _run_single_user(self) -> None:
        uid = self._single_user_id
        assert uid is not None
        user = await db_mod.get_user(self.service.db_path, uid)
        name = (user or {}).get("name") or f"User{uid}"

        print(f"\n🤖 pAIrtner CLI (single-user mode, user {uid})")
        print("Type a message, /command, or 'quit' to exit.\n")

        while self._running:
            try:
                line = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input(f"{name}> "),
                )
            except (EOFError, KeyboardInterrupt):
                break

            line = line.strip()
            if not line:
                continue
            if line.lower() in ("quit", "exit", "/quit"):
                break

            responses = await self._dispatch(uid, name, line)
            for resp in responses:
                target = resp.target_user_id or uid
                await self._print_response(target, resp)

    async def _run_two_user(self) -> None:
        users = [
            (_USER_A_ID, "UserA"),
            (_USER_B_ID, "UserB"),
        ]
        turn = 0

        print("\n🤖 pAIrtner CLI (two-user mode)")
        print("Alternating between UserA and UserB.")
        print("Type a message, /command, or 'quit' to exit.\n")

        while self._running:
            uid, name = users[turn % 2]
            try:
                line = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input(f"{name}> "),
                )
            except (EOFError, KeyboardInterrupt):
                break

            line = line.strip()
            if not line:
                continue
            if line.lower() in ("quit", "exit", "/quit"):
                break

            responses = await self._dispatch(uid, name, line)
            for resp in responses:
                target = resp.target_user_id or uid
                await self._print_response(target, resp)

            turn += 1

    async def _dispatch(self, uid: int, name: str, text: str):
        """Route text through the shared BotService dispatcher."""
        return await self.service.dispatch(uid, name, text)

    async def _print_response(self, target_uid: int, resp) -> None:
        """Print a BotResponse to stdout."""
        await self.send_message(
            target_uid,
            resp.text,
            buttons=(
                [[(b.text, b.callback_data) for b in row]
                 for row in resp.buttons]
                if resp.buttons else None
            ),
            document=resp.document,
        )
