from __future__ import annotations

import asyncio
from collections import defaultdict
from threading import Lock


class LiveEventBus:
    def __init__(self) -> None:
        self._lock = Lock()
        self._versions: dict[int, int] = defaultdict(int)
        self._waiters: dict[int, set[tuple[asyncio.AbstractEventLoop, asyncio.Future[int]]]] = defaultdict(set)

    def publish(self, family_id: int) -> int:
        with self._lock:
            self._versions[family_id] += 1
            version = self._versions[family_id]
            waiters = list(self._waiters.pop(family_id, set()))
        for loop, future in waiters:
            try:
                loop.call_soon_threadsafe(self._resolve_waiter, future, version)
            except RuntimeError:
                # Der SSE-Client kann genau zwischen dem Entfernen der Waiter
                # und der Zustellung seinen Event-Loop schließen.
                continue
        return version

    def current_version(self, family_id: int) -> int:
        with self._lock:
            return self._versions.get(family_id, 0)

    async def wait_for_update(self, family_id: int, known_version: int, timeout: float) -> int:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[int] = loop.create_future()
        waiter = (loop, future)
        with self._lock:
            current = self._versions.get(family_id, 0)
            if current > known_version:
                return current
            self._waiters[family_id].add(waiter)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            return self.current_version(family_id)
        finally:
            with self._lock:
                family_waiters = self._waiters.get(family_id)
                if family_waiters is not None:
                    family_waiters.discard(waiter)
                    if not family_waiters:
                        self._waiters.pop(family_id, None)

    @staticmethod
    def _resolve_waiter(future: asyncio.Future[int], version: int) -> None:
        if not future.done():
            future.set_result(version)


live_event_bus = LiveEventBus()
