"""MultiAdapter: run several transports with one set of background loops."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import BaseAdapter

logger = logging.getLogger(__name__)


class MultiAdapter(BaseAdapter):
    """Starts child adapters concurrently and runs the loops exactly once.

    Children must be built with ``run_background=False`` so they do not start
    their own loops. Outgoing loop messages are routed to the first child whose
    ``owns_user`` accepts the user id.
    """

    def __init__(self, children: list[BaseAdapter]) -> None:
        if not children:
            raise ValueError("MultiAdapter needs at least one child adapter")
        super().__init__(children[0].service, children[0].config)
        self.children = list(children)
        self.transport_description = " and ".join(
            c.transport_description for c in self.children
        )
        for child in self.children:
            api = getattr(child, "api", None)
            if api is not None:
                api.forward = self._forward

    def _owner(self, user_id: int | str) -> BaseAdapter | None:
        uid = int(user_id)
        for child in self.children:
            if child.owns_user(uid):
                return child
        return None

    def owns_user(self, user_id: int) -> bool:
        return self._owner(user_id) is not None

    async def send_message(
        self,
        user_id: int | str,
        text: str,
        buttons: list[list[tuple[str, str]]] | None = None,
        document: tuple[str, str] | None = None,
    ) -> None:
        owner = self._owner(user_id)
        if owner is None:
            logger.warning("No adapter owns user %s; message dropped", user_id)
            return
        await owner.send_message(user_id, text, buttons=buttons, document=document)

    async def send_typing(self, user_id: int | str) -> None:
        owner = self._owner(user_id)
        if owner is not None:
            await owner.send_typing(user_id)

    async def _forward(self, user_id: int, msg: dict[str, Any]) -> None:
        """Deliver a REST-originated response to a user owned by another child."""
        buttons = [[(b["text"], b["data"]) for b in row] for row in msg["buttons"]]
        doc = msg.get("document")
        await self.send_message(
            user_id, msg["markup"], buttons=buttons or None,
            document=(doc["filename"], doc["content"]) if doc else None,
        )

    async def start(self) -> None:
        await self._start_background_tasks()
        try:
            await asyncio.gather(*(c.start() for c in self.children))
        finally:
            await self._cancel_background_tasks()

    async def stop(self) -> None:
        await self._cancel_background_tasks()
        await asyncio.gather(*(c.stop() for c in self.children))
