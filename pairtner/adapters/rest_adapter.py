"""REST adapter: JSON over HTTP with a durable outbox, plus static PWA files.

The HTTP server is the stdlib ``ThreadingHTTPServer`` in a daemon thread; each
request is bridged into the bot's event loop with
``asyncio.run_coroutine_threadsafe``. All logic lives in
:class:`pairtner.rest.api.RestApi`.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _QuietHTTPServer(ThreadingHTTPServer):
    """Suppress BrokenPipeError tracebacks from client disconnects."""

    def handle_error(self, request, client_address):
        import sys
        exc_type = sys.exc_info()[0]
        if exc_type is BrokenPipeError:
            return  # client disconnected -- normal for SSE, page navigation
        super().handle_error(request, client_address)

from pairtner import db as db_mod
from pairtner.rest.api import MAX_BODY_BYTES, Request, RestApi, text_to_msg
from pairtner.rest.ids import REST_ID_BASE

from .base import BaseAdapter

logger = logging.getLogger(__name__)

_PRUNE_INTERVAL_SECONDS = 24 * 3600
_REQUEST_SOCKET_TIMEOUT = 30


class RestAdapter(BaseAdapter):
    """Serves BotService over HTTP; partner/loop messages go to a durable outbox."""

    transport_description = (
        "this server over HTTP (no third-party messaging service)"
    )

    def __init__(self, service, config, run_background: bool = True) -> None:
        super().__init__(service, config)
        self.run_background = run_background
        self.api = RestApi(service, config, hooks=self)
        self.ready = asyncio.Event()
        self.bound_port: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._stop_event: asyncio.Event | None = None

    def owns_user(self, user_id: int) -> bool:
        return int(user_id) >= REST_ID_BASE

    # ── BaseAdapter interface ────────────────────────────────────────────

    async def send_message(
        self,
        user_id: int | str,
        text: str,
        buttons: list[list[tuple[str, str]]] | None = None,
        document: tuple[str, str] | None = None,
    ) -> None:
        await self.api.publish(int(user_id), text_to_msg(text, buttons, document))

    async def send_typing(self, user_id: int | str) -> None:
        return None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        await db_mod.init_db(self.service.db_path)
        await self._prune()
        self._background_tasks.append(asyncio.create_task(self._prune_loop()))
        if self.run_background:
            await self._start_background_tasks()

        server = _QuietHTTPServer(
            (self.config.rest_host, self.config.rest_port),
            _make_handler(self),
        )
        server.daemon_threads = True
        self._server = server
        self.bound_port = server.server_address[1]
        self._thread = threading.Thread(
            target=server.serve_forever, name="rest-http", daemon=True,
        )
        self._thread.start()
        logger.info(
            "REST adapter listening on http://%s:%d (registration: %s)",
            self.config.rest_host, self.bound_port, self.config.rest_registration,
        )
        self.ready.set()
        await self._stop_event.wait()

    async def stop(self) -> None:
        await self._cancel_background_tasks()
        server, self._server = self._server, None
        if server is not None:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
        if self._stop_event:
            self._stop_event.set()

    # ── internals ────────────────────────────────────────────────────────

    async def _prune(self) -> None:
        removed = await db_mod.prune_outbox(
            self.service.db_path, self.config.rest_event_retention_days)
        if removed:
            logger.info("Pruned %d old REST outbox events", removed)

    async def _prune_loop(self) -> None:
        while True:
            await asyncio.sleep(_PRUNE_INTERVAL_SECONDS)
            await self._prune()

    def run_request(self, request: Request):
        """Run the API coroutine on the bot loop from a handler thread."""
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(self.api.handle(request), self._loop)
        timeout = self.config.rest_long_poll_max_seconds + 30
        return future.result(timeout=timeout)


def _make_handler(adapter: RestAdapter) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        timeout = _REQUEST_SOCKET_TIMEOUT
        server_version = "pairtner"
        sys_version = ""

        def _handle(self) -> None:
            body = b""
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0:
                self._send_error_json(400, "invalid_length")
                return
            if length:
                # Read at most one byte past the limit; the API answers 413.
                body = self.rfile.read(min(length, MAX_BODY_BYTES + 1))
            headers = {k.lower(): v for k, v in self.headers.items()}
            request = Request(
                self.command, self.path, headers, body, self.client_address[0],
            )
            try:
                resp = adapter.run_request(request)
            except concurrent.futures.TimeoutError:
                logger.warning("REST request timed out: %s", self.command)
                self._send_error_json(504, "timeout")
                return
            except Exception:  # request boundary: log, answer 500, keep serving
                logger.exception("REST request failed")
                self._send_error_json(500, "internal")
                return
            try:
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.content_type)
                self.send_header("Content-Length", str(len(resp.body)))
                for key, value in resp.headers:
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(resp.body)
            except BrokenPipeError:
                pass  # client disconnected mid-response (normal for SSE, page nav)

        def _send_error_json(self, status: int, code: str) -> None:
            payload = json.dumps({"error": code}).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _handle

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            logger.debug("REST %s", format % args)

    return Handler
