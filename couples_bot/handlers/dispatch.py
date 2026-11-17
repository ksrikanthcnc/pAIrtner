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
            "start": lambda: self.handle_start(uid, name),
            "join": lambda: self.handle_join(uid, name, arg),
            "tell": lambda: self.handle_tell(uid, arg),
            "mediate": lambda: self.handle_mediate(uid, arg),
            "whatdoyouknow": lambda: self.handle_whatdoyouknow(uid),
            "forget": lambda: self.handle_forget(uid, arg),
            "export": lambda: self.handle_export(uid, arg),
            "help": lambda: self.handle_help(uid),
            "lang": lambda: self.handle_lang(uid, arg),
            "setup": lambda: self.handle_setup(uid, arg),
            "status": lambda: self.handle_status(uid),
            "usage": lambda: self.handle_usage(uid, arg),
            "history": lambda: self.handle_history(uid, arg),
            "pause": lambda: self.handle_pause(uid),
            "resume": lambda: self.handle_resume(uid),
            "style": lambda: self.handle_style(uid, arg),
            "vibe": lambda: self.handle_vibe(uid, arg),
            "mood": lambda: self.handle_mood(uid, arg),
            "goal": lambda: self.handle_goal(uid, arg),
            "together": lambda: self.handle_together(uid, arg),
            "goals": lambda: self.handle_goals(uid),
            "settings": lambda: self.handle_settings(uid, arg),
            "remember": lambda: self.handle_remember(uid),
            "review": lambda: self.handle_review(uid),
            "search": lambda: self.handle_search(uid, arg),
            "conflicts": lambda: self.handle_conflicts(uid),
            "timeline": lambda: self.handle_timeline(uid, arg),
            # /skip is consumed by the onboarding intercept inside handle_message.
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
