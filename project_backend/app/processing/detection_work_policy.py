"""Request-local diagnostic work policy; concurrent Admin requests stay intact."""

from contextvars import ContextVar
from functools import wraps

_USER_DETECTION = ContextVar('user_detection', default=False)
_COMPACT_CANDIDATES = ContextVar('compact_detection_candidates', default=False)


def include_detection_diagnostics() -> bool:
    return not _USER_DETECTION.get()


def compact_detection_candidates() -> bool:
    return _COMPACT_CANDIDATES.get()


def detection_work_scope(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        token = _USER_DETECTION.set(bool(kwargs.get('user_context', False)))
        compact_token = _COMPACT_CANDIDATES.set(bool(kwargs.get('compact_candidates', False)))
        try:
            return function(*args, **kwargs)
        finally:
            _COMPACT_CANDIDATES.reset(compact_token)
            _USER_DETECTION.reset(token)
    return wrapped
