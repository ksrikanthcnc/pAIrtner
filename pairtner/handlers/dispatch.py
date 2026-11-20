"""Single place that maps ``/command args`` text onto BotService handlers.

Adapters that receive raw text (CLI, WebSocket, future PWA) call
:meth:`DispatchMixin.dispatch` instead of keeping their own command tables, so a
new command only needs to be registered here.  The Telegram adapter registers
commands with Telegram's own handler API and is not routed through this.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from .models import BotResponse


class DispatchMixin:
    """Mixin for :class:`BotService`: text -> command handler routing."""

    def _command_table(
        self, uid: int, name: str, arg: str | None
    ) -> dict[str, Callable[[], Awaitable[list[BotResponse]]]]:
        """Return ``{command: zero-arg coroutine factory}`` for one invocation."""
        return {
            # Pairing
            "start": lambda: self.handle_start(uid, name),
            "join": lambda: self.handle_join(uid, name, arg),
            # Core features
            "tell": lambda: self.handle_tell(uid, arg),
            "mediate": lambda: self.handle_mediate(uid, arg),
            # Memory
            "whatdoyouknow": lambda: self.handle_whatdoyouknow(uid),
            "forget": lambda: self.handle_forget(uid, arg),
            "review": lambda: self.handle_review(uid),
            "search": lambda: self.handle_search(uid, arg),
            "remember": lambda: self.handle_remember(uid),
            # Style: voice + mood unified
            "style": lambda: self.handle_style(uid, arg),
            # Goals: personal + shared unified
            "goal": lambda: self.handle_goal(uid, arg),
            # Settings & config
            "settings": lambda: self.handle_settings(uid, arg),
            "mode": lambda: self.handle_mode(uid, arg),
            "yolo": lambda: self.handle_yolo(uid, arg),
            "lang": lambda: self.handle_lang(uid, arg),
            "setup": lambda: self.handle_setup(uid, arg),
            # Status & info
            "status": lambda: self.handle_status(uid),
            "usage": lambda: self.handle_usage(uid, arg),
            "help": lambda: self.handle_help(uid),
            "export": lambda: self.handle_export(uid, arg),
            "history": lambda: self.handle_history(uid, arg),
            # Testing
            "dryrun": lambda: self.handle_dryrun(uid, arg),
            "debug": lambda: self.handle_debug(uid, arg),
            # Relay control
            "pause": lambda: self.handle_pause(uid),
            "resume": lambda: self.handle_resume(uid),
            # Advanced
            "conflicts": lambda: self.handle_conflicts(uid),
            "timeline": lambda: self.handle_timeline(uid, arg),
            # Onboarding skip
            "skip": lambda: self.handle_message(uid, name, "/skip"),
        }

    async def dispatch(self, user_id: int, name: str, text: str) -> list[BotResponse]:
        """Route raw user text: ``/cmd [args]`` to a command, anything else to chat."""
        text = text.strip()
        if not text.startswith("/"):
            return await self.handle_message(user_id, name, text)

        parts = text.split(maxsplit=1)
        # Telegram group syntax "/cmd@botname" -> "cmd"
        cmd = parts[0][1:].split("@", 1)[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else None

        handler = self._command_table(user_id, name, arg).get(cmd)
        if handler is None:
            return [BotResponse(f"Unknown command: /{cmd}")]
        return await handler()
