import functools
import time

from typing import Any, Dict, Optional

from .env import ENV


class Cache:
    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._timestamps: Dict[str, float] = {}

    def get(self, key: str) -> Optional[Any]:
        if key in self._cache:
            if time.time() - self._timestamps[key] < ENV.CACHE_TTL:
                return self._cache[key]
            else:
                del self._cache[key]
                del self._timestamps[key]
        return None

    def set(self, key: str, value: Any):
        self._cache[key] = value
        self._timestamps[key] = time.time()

    def invalidate(self, key: str):
        if key in self._cache:
            del self._cache[key]
            del self._timestamps[key]


_cache = Cache()


def cache_result(ttl: int = ENV.CACHE_TTL):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            cache_key = f"{func.__name__}:{str(args)}:{str(kwargs)}"
            result = _cache.get(cache_key)
            if result is not None:
                return result
            result = await func(*args, **kwargs)
            _cache.set(cache_key, result)
            return result

        return wrapper

    return decorator
