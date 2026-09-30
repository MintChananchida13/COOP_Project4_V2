import threading
import time
from typing import Any, Dict, Optional, Tuple


class MaintenanceEventHub:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._version = 0
        self._payload: Optional[Dict[str, Any]] = None

    def publish(self, payload: Dict[str, Any]) -> int:
        with self._condition:
            self._version += 1
            self._payload = dict(payload)
            self._condition.notify_all()
            return self._version

    def current_version(self) -> int:
        with self._condition:
            return self._version

    def wait_for_event(self, last_seen_version: int, timeout_seconds: float = 25.0) -> Optional[Tuple[int, Dict[str, Any]]]:
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            while self._version <= last_seen_version:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(timeout=remaining)
            return self._version, dict(self._payload or {})


maintenance_event_hub = MaintenanceEventHub()
