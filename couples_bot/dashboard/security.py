"""Token authentication, signed session cookies, CSRF tokens, login throttle.

The cookie carries an expiry and an HMAC signature, never the token itself.
"""
from __future__ import annotations

import hashlib
import hmac
import threading
import time
from typing import Callable

COOKIE_NAME = "pd_session"
BEARER_SESSION = "bearer"


class Authenticator:
    def __init__(
        self,
        token: str,
        session_ttl_seconds: float,
        max_failures: int = 5,
        lockout_seconds: float = 300.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._token = token.encode()
        self._key = hmac.new(self._token, b"pairtner-dashboard-v1", hashlib.sha256).digest()
        self._ttl = session_ttl_seconds
        self._max_failures = max_failures
        self._lockout = lockout_seconds
        self._clock = clock
        self._failures: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    # -- token ---------------------------------------------------------
    def check_token(self, candidate: str) -> bool:
        return hmac.compare_digest(candidate.encode(), self._token)

    # -- sessions ------------------------------------------------------
    def _sign(self, message: str) -> str:
        return hmac.new(self._key, message.encode(), hashlib.sha256).hexdigest()

    def issue_session(self) -> str:
        expires = int(self._clock() + self._ttl)
        return f"{expires}.{self._sign(f'session:{expires}')}"

    def verify_session(self, cookie: str | None) -> bool:
        if not cookie or "." not in cookie:
            return False
        expires, _, sig = cookie.partition(".")
        if not expires.isdigit():
            return False
        if not hmac.compare_digest(sig, self._sign(f"session:{expires}")):
            return False
        return int(expires) > self._clock()

    # -- CSRF ----------------------------------------------------------
    def csrf_token(self, session_id: str) -> str:
        return self._sign(f"csrf:{session_id}")

    def verify_csrf(self, session_id: str, supplied: str | None) -> bool:
        if not supplied:
            return False
        return hmac.compare_digest(supplied, self.csrf_token(session_id))

    # -- throttle ------------------------------------------------------
    def is_locked(self, client: str) -> bool:
        with self._lock:
            count, until = self._failures.get(client, (0, 0.0))
            if count >= self._max_failures:
                if self._clock() < until:
                    return True
                del self._failures[client]
            return False

    def record_failure(self, client: str) -> None:
        with self._lock:
            count, _ = self._failures.get(client, (0, 0.0))
            self._failures[client] = (count + 1, self._clock() + self._lockout)

    def record_success(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)
