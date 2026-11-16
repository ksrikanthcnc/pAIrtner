"""WebSocket adapter — JSON-based protocol for web/app clients."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets

from couples_bot import db as db_mod
from couples_bot.config import is_user_allowed

from .base import BaseAdapter

logger = logging.getLogger(__name__)

try:
    import websockets  # type: ignore[import-untyped]
    import websockets.asyncio.server  # type: ignore[import-untyped]
except ImportError:
    websockets = None  # type: ignore[assignment]


class WebSocketAdapter(BaseAdapter):
    """Adapter that serves BotService over a WebSocket server.

    Protocol:
      Client → Server:
        {"type": "auth", "user_id": "...", "token": "..."}
        {"type": "message", "user_id": "...", "text": "..."}
      Server → Client:
        {"type": "reply", "text": "...", "buttons": [...]}
        {"type": "typing", "active": true/false}
        {"type": "relay", "from": "partner", "text": "..."}
        {"type": "error", "text": "..."}
    """

    def __init__(self, service, config) -> None:
        super().__init__(service, config)
        self._connections: dict[str, object] = {}  # user_id -> websocket
        self._server = None
        self._stop_event: asyncio.Event | None = None

    async def start(self) -> None:
        if websockets is None:
            raise RuntimeError(
                "websockets package is required for WebSocketAdapter. "
                "Install with: uv add websockets"
            )

        self._stop_event = asyncio.Event()
        await self._start_background_tasks()

        port = self.config.ws_port
        logger.info("WebSocket server starting on port %d", port)

        async with websockets.asyncio.server.serve(
            self._handle_connection, "0.0.0.0", port
        ) as server:
            self._server = server
            logger.info("WebSocket server listening on ws://0.0.0.0:%d", port)
            await self._stop_event.wait()

    async def stop(self) -> None:
        await self._cancel_background_tasks()
        if self._stop_event:
            self._stop_event.set()

    async def send_message(
        self,
        user_id: int | str,
        text: str,
        buttons: list[list[tuple[str, str]]] | None = None,
        document: tuple[str, str] | None = None,
    ) -> None:
        uid = str(user_id)
        ws = self._connections.get(uid)
        if ws is None:
            logger.debug("No WS connection for user %s, message dropped", uid)
            return

        msg: dict = {"type": "reply", "text": text}
        if buttons:
            msg["buttons"] = [
                [{"text": btn_text, "callback_data": btn_data}
                 for btn_text, btn_data in row]
                for row in buttons
            ]
        if document:
            msg["document"] = {"filename": document[0], "content": document[1]}

        try:
            await ws.send(json.dumps(msg))
        except Exception:
            logger.debug("Failed to send to user %s, removing connection", uid)
            self._connections.pop(uid, None)

    async def send_typing(self, user_id: int | str) -> None:
        uid = str(user_id)
        ws = self._connections.get(uid)
        if ws is None:
            return
        try:
            await ws.send(json.dumps({"type": "typing", "active": True}))
        except Exception:
            pass

    # ── internal ─────────────────────────────────────────────────────────

    async def _handle_connection(self, ws) -> None:
        """Handle a single WebSocket client connection."""
        authenticated_user: str | None = None

        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    await ws.send(json.dumps({
                        "type": "error", "text": "Invalid JSON",
                    }))
                    continue

                msg_type = data.get("type")

                if msg_type == "auth":
                    authenticated_user = await self._handle_auth(ws, data)
                elif msg_type == "message":
                    if not authenticated_user:
                        await ws.send(json.dumps({
                            "type": "error", "text": "Not authenticated",
                        }))
                        continue
                    await self._handle_ws_message(ws, authenticated_user, data)
                elif msg_type == "callback":
                    if not authenticated_user:
                        await ws.send(json.dumps({
                            "type": "error", "text": "Not authenticated",
                        }))
                        continue
                    await self._handle_ws_callback(ws, authenticated_user, data)
                else:
                    await ws.send(json.dumps({
                        "type": "error", "text": f"Unknown type: {msg_type}",
                    }))
        except Exception:
            logger.debug("WebSocket connection closed for user %s", authenticated_user)
        finally:
            if authenticated_user:
                self._connections.pop(authenticated_user, None)

    async def _handle_auth(self, ws, data: dict) -> str | None:
        """Authenticate a client. Returns user_id string on success."""
        user_id_raw = data.get("user_id")
        token = data.get("token")

        if not user_id_raw or not token:
            await ws.send(json.dumps({
                "type": "error", "text": "auth requires user_id and token",
            }))
            return None

        uid_str = str(user_id_raw)

        # Validate token against DB
        valid = await db_mod.validate_auth_token(
            self.service.db_path, int(user_id_raw), token,
        )
        if not valid:
            await ws.send(json.dumps({
                "type": "error", "text": "Invalid token",
            }))
            return None

        # Check allow-list
        if not is_user_allowed(self.config, int(user_id_raw)):
            await ws.send(json.dumps({
                "type": "error", "text": "User not allowed",
            }))
            return None

        self._connections[uid_str] = ws
        await ws.send(json.dumps({
            "type": "auth_ok", "user_id": uid_str,
        }))
        logger.info("WebSocket client authenticated: user %s", uid_str)
        return uid_str

    async def _handle_ws_message(self, ws, user_id: str, data: dict) -> None:
        """Route an incoming text message to BotService."""
        text = data.get("text", "").strip()
        if not text:
            return

        uid = int(user_id)
        user = await db_mod.get_user(self.service.db_path, uid)
        user_name = (user or {}).get("name") or f"User{uid}"

        # Send typing indicator
        await ws.send(json.dumps({"type": "typing", "active": True}))

        responses = await self.service.dispatch(uid, user_name, text)

        await ws.send(json.dumps({"type": "typing", "active": False}))

        for resp in responses:
            target = resp.target_user_id or uid
            msg: dict = {"type": "reply", "text": resp.text}
            if resp.buttons:
                msg["buttons"] = [
                    [{"text": b.text, "callback_data": b.callback_data}
                     for b in row]
                    for row in resp.buttons
                ]
            if resp.document:
                msg["document"] = {
                    "filename": resp.document[0],
                    "content": resp.document[1],
                }

            if target != uid:
                # Partner relay — send via their WS connection if connected
                relay_msg = {
                    "type": "relay",
                    "from": "partner",
                    "text": resp.text,
                }
                partner_ws = self._connections.get(str(target))
                if partner_ws:
                    try:
                        await partner_ws.send(json.dumps(relay_msg))
                    except Exception:
                        pass
            else:
                await ws.send(json.dumps(msg))

    async def _handle_ws_callback(self, ws, user_id: str, data: dict) -> None:
        """Route an incoming callback (button press) to BotService."""
        callback_data = data.get("callback_data", "").strip()
        if not callback_data:
            return

        uid = int(user_id)
        responses = await self.service.handle_callback(uid, callback_data)

        for resp in responses:
            target = resp.target_user_id or uid
            msg: dict = {"type": "reply", "text": resp.text}
            if resp.buttons:
                msg["buttons"] = [
                    [{"text": b.text, "callback_data": b.callback_data}
                     for b in row]
                    for row in resp.buttons
                ]
            if target != uid:
                partner_ws = self._connections.get(str(target))
                if partner_ws:
                    try:
                        await partner_ws.send(json.dumps({
                            "type": "relay", "from": "partner", "text": resp.text,
                        }))
                    except Exception:
                        pass
            else:
                await ws.send(json.dumps(msg))
