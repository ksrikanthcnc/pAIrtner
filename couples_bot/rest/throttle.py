"""Small in-memory sliding-window rate limiter keyed by an arbitrary string."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Callable


class SlidingWindowLimiter:
    """Allow at most *limit* hits per *window* seconds per key. Thread-safe."""

    def __init__(
        self, limit: int, window: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window = window
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, key: str, now: float) -> deque[float]:
        q = self._hits[key]
        while q and now - q[0] >= self.window:
            q.popleft()
        if not q:
            self._hits.pop(key, None)
            return deque()
        return q

    def hit(self, key: str) -> bool:
        """Record a hit; return False when the key is over its limit."""
        now = self._clock()
        with self._lock:
            q = self._trim(key, now)
            if len(q) >= self.limit:
                return False
            if key not in self._hits:
                self._hits[key] = q
            q.append(now)
            return True

    def blocked(self, key: str) -> bool:
        """True when the key has used up its allowance (without recording)."""
        now = self._clock()
        with self._lock:
            return len(self._trim(key, now)) >= self.limit
