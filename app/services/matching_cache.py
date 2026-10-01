"""Request-scoped memoization for repeated deterministic OCR-text matching."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from hashlib import sha256
from collections import OrderedDict
from threading import RLock
from typing import Any, Callable, Iterator, TypeVar

from contextlib import contextmanager


_MAX_ENTRIES = 32_768
_HASH_CACHE_MAX_ENTRIES = 32_768
_CURRENT_CACHE: ContextVar["MatchingCache | None"] = ContextVar("ocr_matching_cache", default=None)
_HASH_CACHE: OrderedDict[tuple[Any, ...], Any] = OrderedDict()
_HASH_CACHE_LOCK = RLock()
_T = TypeVar("_T")


@dataclass
class MatchingCache:
    values: dict[tuple[Any, ...], Any] = field(default_factory=dict)
    hits: int = 0
    misses: int = 0

    def clear(self) -> None:
        self.values.clear()
        self.hits = 0
        self.misses = 0


@contextmanager
def matching_cache_scope() -> Iterator[MatchingCache]:
    """Share matching results within one request and clear OCR text at exit."""
    current = _CURRENT_CACHE.get()
    if current is not None:
        yield current
        return

    cache = MatchingCache()
    token = _CURRENT_CACHE.set(cache)
    try:
        yield cache
    finally:
        cache.clear()
        _CURRENT_CACHE.reset(token)


def with_matching_cache(function: Callable[..., _T]) -> Callable[..., _T]:
    """Give a synchronous document-processing entry point a private cache."""
    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> _T:
        with matching_cache_scope():
            return function(*args, **kwargs)

    return wrapped


def memoized(key: tuple[Any, ...], compute: Callable[[], _T]) -> _T:
    """Memoize only while a document-processing scope is active."""
    cache = _CURRENT_CACHE.get()
    if cache is None:
        return compute()
    if key in cache.values:
        cache.hits += 1
        return cache.values[key]
    cache.misses += 1
    value = compute()
    if len(cache.values) < _MAX_ENTRIES:
        cache.values[key] = value
    return value


def hashed_memoized(key: tuple[Any, ...], compute: Callable[[], _T]) -> _T:
    """Cache pure matching results using digests, never retaining OCR text keys."""
    safe_key = _hashed_key(key)
    with _HASH_CACHE_LOCK:
        if safe_key in _HASH_CACHE:
            _HASH_CACHE.move_to_end(safe_key)
            return _HASH_CACHE[safe_key]

    value = compute()
    with _HASH_CACHE_LOCK:
        existing = _HASH_CACHE.get(safe_key)
        if existing is not None:
            _HASH_CACHE.move_to_end(safe_key)
            return existing
        _HASH_CACHE[safe_key] = value
        if len(_HASH_CACHE) > _HASH_CACHE_MAX_ENTRIES:
            _HASH_CACHE.popitem(last=False)
    return value


def _hashed_key(value: Any) -> Any:
    if isinstance(value, str):
        return sha256(value.encode("utf-8", errors="replace")).digest()
    if isinstance(value, tuple):
        return tuple(_hashed_key(item) for item in value)
    if value is None or isinstance(value, (int, float, bool, bytes)):
        return value
    raise TypeError(f"Unsupported cache key type: {type(value).__name__}")
