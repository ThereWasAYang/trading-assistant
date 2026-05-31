"""智能缓存模块 — 带TTL过期和内存在线缓存，避免频繁请求被封IP"""

import time
from threading import Lock
from typing import Any, Callable, Optional

from utils.logger import get_logger

logger = get_logger(__name__)


class TTLCache:
    """带TTL的内存缓存"""

    def __init__(self, default_ttl: float = 30.0):
        """
        default_ttl: 默认过期时间(秒)
        """
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = Lock()
        self._default_ttl = default_ttl
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        """获取缓存值，过期返回None"""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            expires_at, value = entry
            if time.time() > expires_at:
                del self._store[key]
                self._misses += 1
                return None
            self._hits += 1
            return value

    def set(self, key: str, value: Any, ttl: float = None) -> None:
        """设置缓存值"""
        if ttl is None:
            ttl = self._default_ttl
        with self._lock:
            self._store[key] = (time.time() + ttl, value)

    def delete(self, key: str) -> None:
        """删除缓存"""
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        """清空所有缓存"""
        with self._lock:
            self._store.clear()

    def stats(self) -> dict:
        """缓存统计"""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = self._hits / total if total > 0 else 0.0
            return {
                "entries": len(self._store),
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(hit_rate, 3),
            }


# ============================================================
# 全局缓存实例
# ============================================================

# 实时行情缓存 (5秒过期，因为数据更新频率高)
_quotes_cache = TTLCache(default_ttl=5.0)

# K线数据缓存 (5分钟过期，K线变化慢)
_kline_cache = TTLCache(default_ttl=300.0)

# 搜索缓存 (10分钟过期，股票列表变化不大)
_search_cache = TTLCache(default_ttl=600.0)

# 30分钟K线缓存 (2分钟过期)
_kline_30min_cache = TTLCache(default_ttl=120.0)


def get_quotes_cache() -> TTLCache:
    return _quotes_cache


def get_kline_cache() -> TTLCache:
    return _kline_cache


def get_search_cache() -> TTLCache:
    return _search_cache


def get_30min_cache() -> TTLCache:
    return _kline_30min_cache


def cached(ttl: float = 30.0):
    """装饰器：自动缓存函数返回值（基于参数生成缓存key）"""

    def decorator(func: Callable):
        def wrapper(*args, **kwargs):
            cache_key = f"{func.__name__}:{str(args)}:{str(kwargs)}"
            # 使用全局通用缓存（可以扩展为每个函数独立缓存）
            result = _kline_cache.get(cache_key)
            if result is not None:
                logger.debug(f"缓存命中: {cache_key[:60]}")
                return result
            result = func(*args, **kwargs)
            _kline_cache.set(cache_key, result, ttl)
            return result

        return wrapper

    return decorator
