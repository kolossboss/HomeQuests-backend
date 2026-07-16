from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import hashlib
import heapq
from threading import Lock
import time

from .config import settings


@dataclass
class _AttemptState:
    attempts: deque[float] = field(default_factory=deque)
    blocked_until: float = 0.0
    last_seen: float = 0.0


class LoginRateLimiter:
    _MAX_KEYS = 10_000

    def __init__(self) -> None:
        self._lock = Lock()
        self._states: dict[str, _AttemptState] = {}

    @staticmethod
    def key(client_host: str, identifier: str) -> str:
        raw = f"{client_host.strip().lower()}\0{identifier.strip().lower()}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def retry_after(self, key: str) -> int:
        now = time.monotonic()
        with self._lock:
            state = self._states.get(key)
            if state is None:
                return 0
            self._prune_attempts(state, now)
            state.last_seen = now
            if state.blocked_until <= now:
                state.blocked_until = 0.0
                return 0
            return max(1, int(state.blocked_until - now) + 1)

    def record_failure(self, key: str) -> int:
        now = time.monotonic()
        with self._lock:
            state = self._states.setdefault(key, _AttemptState())
            self._prune_attempts(state, now)
            state.attempts.append(now)
            state.last_seen = now
            if len(state.attempts) >= settings.login_rate_limit_attempts:
                state.blocked_until = now + settings.login_rate_limit_block_seconds
                state.attempts.clear()
            self._evict_if_needed()
            if state.blocked_until > now:
                return max(1, int(state.blocked_until - now) + 1)
            return 0

    def clear(self, key: str) -> None:
        with self._lock:
            self._states.pop(key, None)

    @staticmethod
    def _prune_attempts(state: _AttemptState, now: float) -> None:
        cutoff = now - settings.login_rate_limit_window_seconds
        while state.attempts and state.attempts[0] < cutoff:
            state.attempts.popleft()

    def _evict_if_needed(self) -> None:
        overflow = len(self._states) - self._MAX_KEYS
        if overflow <= 0:
            return
        # Nicht bei jedem neuen Schlüssel alle 10.000 Einträge sortieren. Eine
        # kleine Batch-Räumung hält den Speicher begrenzt und den Angriffsfall O(n).
        batch_size = max(overflow, self._MAX_KEYS // 10)
        oldest = heapq.nsmallest(batch_size, self._states.items(), key=lambda item: item[1].last_seen)
        for key, _ in oldest:
            self._states.pop(key, None)


login_rate_limiter = LoginRateLimiter()
