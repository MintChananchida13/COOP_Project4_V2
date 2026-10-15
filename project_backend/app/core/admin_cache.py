"""Small process-local cache for read-heavy Admin metadata endpoints."""

from copy import deepcopy
import builtins
import os
import threading
import time
from collections import OrderedDict
from typing import Any, Optional


ADMIN_CACHE_TTL_SECONDS = max(1.0, float(os.getenv("ADMIN_CACHE_TTL_SECONDS", "1800")))
ADMIN_CACHE_MAX_ENTRIES = max(1, int(os.getenv("ADMIN_CACHE_MAX_ENTRIES", "512")))
_lock = threading.RLock()
_entries: OrderedDict[str, tuple[float, Any]] = OrderedDict()
_key_locks: dict[str, threading.Lock] = {}
_generations: dict[str, int] = {}


def get(key: str) -> Optional[Any]:
    now = time.monotonic()
    with _lock:
        entry = _entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= now:
            _entries.pop(key, None)
            return None
        _entries.move_to_end(key)
        return deepcopy(value)


def set(key: str, value: Any) -> Any:
    with _lock:
        _entries[key] = (time.monotonic() + ADMIN_CACHE_TTL_SECONDS, deepcopy(value))
        _entries.move_to_end(key)
        while len(_entries) > ADMIN_CACHE_MAX_ENTRIES:
            _entries.popitem(last=False)
    return deepcopy(value)


def get_or_load(key: str, loader: Any) -> Any:
    """Load one key once; invalidation during the load prevents stale repopulation."""
    cached = get(key)
    if cached is not None:
        return cached
    with _lock:
        key_lock = _key_locks.setdefault(key, threading.Lock())
        generation = _generations.setdefault(key, 0)
    with key_lock:
        cached = get(key)
        if cached is not None:
            return cached
        value = loader()
        with _lock:
            if _generations.get(key, 0) != generation:
                return deepcopy(value)
        return set(key, value)


def invalidate(*prefixes: str) -> None:
    with _lock:
        if not prefixes:
            _entries.clear()
            _key_locks.clear()
            _generations.clear()
            return
        for key in list(_entries):
            if any(key.startswith(prefix) for prefix in prefixes):
                _entries.pop(key, None)
        known_keys = builtins.set(_entries.keys()) | builtins.set(_generations.keys())
        for key in known_keys:
            if any(key.startswith(prefix) for prefix in prefixes):
                _generations[key] = _generations.get(key, 0) + 1


def stats() -> dict[str, int]:
    with _lock:
        return {"entries": len(_entries), "max_entries": ADMIN_CACHE_MAX_ENTRIES}

