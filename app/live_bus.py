from __future__ import annotations

from collections import defaultdict
from threading import Condition


class LiveEventBus:
    def __init__(self) -> None:
        self._condition = Condition()
        self._versions: dict[int, int] = defaultdict(int)

    def publish(self, family_id: int) -> int:
        with self._condition:
            self._versions[family_id] += 1
            version = self._versions[family_id]
            self._condition.notify_all()
            return version

    def current_version(self, family_id: int) -> int:
        with self._condition:
            return self._versions.get(family_id, 0)

    def wait_for_update(self, family_id: int, known_version: int, timeout: float) -> int:
        with self._condition:
            if self._versions.get(family_id, 0) > known_version:
                return self._versions[family_id]
            self._condition.wait(timeout=timeout)
            return self._versions.get(family_id, 0)


live_event_bus = LiveEventBus()
