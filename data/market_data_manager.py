"""市场数据管理器 — 内存缓存 + DB 双层架构

内存层（热路径，毫秒级）:
  - _quotes: 现价/涨跌幅，每60s刷新
  - _today_bars: 今日日线bar，每60s更新
  - _kline_cache: DB历史 + 今日bar 拼接，懒加载

DB 层（持久化）:
  - 完整日线/周线/月线历史（仅终值）
  - 每5分钟从内存 flush 一次
  - 启动时加载到内存
"""

import threading
from datetime import datetime, timedelta

from data.models import RealtimeQuote, KLineData
from data.market_data import fetch_kline, fetch_single_stock_quote
from data.database import (
    save_klines_batch, get_klines as db_get_klines, get_latest_kline_date,
)
from utils.logger import get_logger

logger = get_logger(__name__)


def _dict_to_kline(d: dict) -> KLineData:
    """将DB返回的dict转为KLineData"""
    return KLineData(
        code=d.get("code", ""),
        date=d.get("date", ""),
        open=float(d.get("open", 0)),
        high=float(d.get("high", 0)),
        low=float(d.get("low", 0)),
        close=float(d.get("close", 0)),
        volume=int(d.get("volume", 0)),
        period=d.get("period", "daily"),
    )


def _dicts_to_klines(dicts: list[dict]) -> list[KLineData]:
    """批量转换"""
    return [_dict_to_kline(d) for d in dicts]


class MarketDataManager:
    """股票市场数据的统一入口

    所有计算模块（买点扫描、预警、图表）通过此管理器获取数据，
    不直接调用 API 或 DB。
    """

    def __init__(self):
        # 现价快照: {code: RealtimeQuote}
        self._quotes: dict[str, RealtimeQuote] = {}
        self._quotes_lock = threading.Lock()

        # 今日日线 bar: {(code, period): dict}
        self._today_bars: dict[tuple[str, str], dict] = {}
        self._today_bars_lock = threading.Lock()

        # K线缓存（历史+今日拼接结果）: {cache_key: list[KLineData]}
        self._kline_cache: dict[str, list[KLineData]] = {}
        self._kline_cache_lock = threading.Lock()

        # 正在初始获取中的股票代码
        self._pending_codes: set[str] = set()
        self._pending_lock = threading.Lock()

        # 上次 flush 时间
        self._last_flush_time = datetime.now()

        # K线缓存TTL (秒)
        self._cache_ttl = 60.0
        self._cache_timestamps: dict[str, float] = {}

    # ================================================================
    # 现价相关
    # ================================================================

    def update_quotes(self, quotes: dict[str, RealtimeQuote]) -> None:
        """批量更新现价快照"""
        with self._quotes_lock:
            self._quotes.update(quotes)

    def get_quote(self, code: str) -> RealtimeQuote | None:
        """获取单只股票的现价快照"""
        with self._quotes_lock:
            return self._quotes.get(code)

    def get_all_quotes(self) -> dict[str, RealtimeQuote]:
        """获取全部现价快照"""
        with self._quotes_lock:
            return dict(self._quotes)

    # ================================================================
    # 启动初始化
    # ================================================================

    def startup_load_quotes(self, codes: list[str]) -> int:
        """启动时从DB的日线末尾加载现价到内存缓存（冷启动，无API调用）
        返回成功加载的股票数量
        """
        loaded = 0
        for code in codes:
            try:
                db_dicts = db_get_klines(code, "daily", days=2)
                if not db_dicts:
                    continue

                last = db_dicts[-1]
                price = last["close"]
                pre_close = price
                if len(db_dicts) >= 2:
                    pre_close = db_dicts[-2]["close"]

                change_pct = ((price - pre_close) / pre_close * 100) if pre_close > 0 else 0.0
                quote = RealtimeQuote(
                    code=code,
                    name="",
                    price=round(price, 2),
                    change_pct=round(change_pct, 2),
                    change_amt=round(price - pre_close, 2),
                    volume=last.get("volume", 0),
                    high=last.get("high", 0),
                    low=last.get("low", 0),
                    open=last.get("open", 0),
                    pre_close=round(pre_close, 2),
                    timestamp=last["date"],
                )
                with self._quotes_lock:
                    self._quotes[code] = quote
                loaded += 1
            except Exception as e:
                logger.debug(f"启动加载 {code} 现价失败: {e}")

        if loaded > 0:
            logger.info(f"启动加载: {loaded}/{len(codes)} 只股票现价从DB恢复")
        return loaded

    # ================================================================
    # 今日 bar
    # ================================================================

    def update_today_bar(self, code: str, period: str, bar: dict) -> None:
        """更新今日K线bar（盘中每次刷新覆盖）"""
        key = (code, period)
        with self._today_bars_lock:
            self._today_bars[key] = bar
        # 使K线缓存失效
        self._invalidate_kline_cache(code, period)

    def get_today_bar(self, code: str, period: str) -> dict | None:
        """获取今日bar"""
        with self._today_bars_lock:
            return self._today_bars.get((code, period))

    def _invalidate_kline_cache(self, code: str, period: str | None = None) -> None:
        """使指定股票的K线缓存失效"""
        periods = [period] if period else ["daily", "weekly", "monthly"]
        with self._kline_cache_lock:
            for p in periods:
                prefix = f"{code}:{p}:"
                stale = [k for k in self._kline_cache if k.startswith(prefix)]
                for k in stale:
                    self._kline_cache.pop(k, None)
                    self._cache_timestamps.pop(k, None)

    # ================================================================
    # K线数据 — 计算模块的主入口
    # ================================================================

    def get_klines(
        self,
        code: str,
        period: str = "daily",
        days: int | None = None,
        force_refresh: bool = False,
    ) -> list[KLineData]:
        """
        获取K线数据 = DB历史 + 内存中的今日bar
        优先从内存缓存返回，缓存过期则从DB重建

        Returns:
            list[KLineData] 按日期升序
        """
        cache_key = f"{code}:{period}:{days or 'all'}"
        import time

        # 检查缓存
        if not force_refresh:
            with self._kline_cache_lock:
                cached = self._kline_cache.get(cache_key)
                ts = self._cache_timestamps.get(cache_key, 0)
                if cached is not None and (time.time() - ts) < self._cache_ttl:
                    return cached

        # 从DB加载 + 拼接今日bar
        result = self._build_klines(code, period, days)

        # 写入缓存
        with self._kline_cache_lock:
            self._kline_cache[cache_key] = result
            self._cache_timestamps[cache_key] = time.time()

        return result

    def _build_klines(
        self, code: str, period: str, days: int | None
    ) -> list[KLineData]:
        """从DB加载历史K线，拼接内存中的今日bar"""
        db_dicts = db_get_klines(code=code, period=period, days=days)
        klines = _dicts_to_klines(db_dicts)
        today = self.get_today_bar(code, period)

        if not today:
            return klines

        today_kline = _dict_to_kline(today)
        today_date = today["date"]

        # 如果DB已有今日数据，用内存中的替换（盘中更新）
        if klines and klines[-1].date == today_date:
            klines[-1] = today_kline
        else:
            klines.append(today_kline)

        # 如果指定了 days，截断
        if days is not None and len(klines) > days:
            klines = klines[-days:]

        return klines

    # ================================================================
    # 新股初始获取 (添加股票时调用)
    # ================================================================

    def is_pending(self, code: str) -> bool:
        """检查股票是否正在初始获取中"""
        with self._pending_lock:
            return code in self._pending_codes

    def mark_pending(self, code: str) -> None:
        """标记为初始获取中"""
        with self._pending_lock:
            self._pending_codes.add(code)

    def unmark_pending(self, code: str) -> None:
        """移除初始获取标记"""
        with self._pending_lock:
            self._pending_codes.discard(code)

    def fetch_and_store_initial(self, code: str, kline_callback=None) -> dict:
        """
        新股初始获取：拉取半年日线，存DB，加载到内存
        返回: {"daily": list[dict], "weekly": list[dict], "monthly": list[dict]}

        kline_callback(code, period, klines) — 每获取完一个周期就回调
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        periods = ["daily", "weekly", "monthly"]
        days_map = {"daily": 126, "weekly": 52, "monthly": 12}
        results = {}

        self.mark_pending(code)

        try:
            # 第一阶段: 并行拉取 (纯 API 调用，不写 DB)
            klines_by_period = {}
            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = {
                    executor.submit(fetch_kline, code, p, days_map[p]): p
                    for p in periods
                }
                for future in as_completed(futures):
                    period = futures[future]
                    try:
                        klines = future.result()
                        klines_by_period[period] = klines

                        # 先发信号通知图表 (数据已在内存)
                        if kline_callback and klines:
                            kline_callback(code, period, klines)

                    except Exception as e:
                        logger.error(f"初始获取 {code} {period} K线失败: {e}")
                        klines_by_period[period] = []

            # 第二阶段: 串行写 DB (避免并发写冲突)
            all_dicts = []
            daily_klines = None
            for period in periods:
                klines = klines_by_period.get(period, [])
                kline_dicts = [
                    {
                        "code": k.code, "date": k.date,
                        "open": k.open, "high": k.high,
                        "low": k.low, "close": k.close,
                        "volume": k.volume, "period": k.period,
                    }
                    for k in klines
                ]
                results[period] = kline_dicts
                all_dicts.extend(kline_dicts)
                if period == "daily":
                    daily_klines = klines

            # 一次性批量写 DB
            if all_dicts:
                try:
                    save_klines_batch(all_dicts)
                    logger.info(f"已存储 {code} K线 {len(all_dicts)} 条 (日/周/月)")
                except Exception as e:
                    logger.error(f"存储 {code} K线到DB失败: {e}")

            # 日线末条 → 现价缓存
            if daily_klines and len(daily_klines) >= 2:
                last = daily_klines[-1]
                prev = daily_klines[-2]
                price = last.close
                pre_close = prev.close
                change_pct = ((price - pre_close) / pre_close * 100) if pre_close > 0 else 0.0
                quote = RealtimeQuote(
                    code=code, name="",
                    price=price,
                    change_pct=round(change_pct, 2),
                    change_amt=round(price - pre_close, 2),
                    volume=last.volume,
                    high=last.high, low=last.low,
                    open=last.open,
                    pre_close=round(pre_close, 2),
                    timestamp=datetime.now().strftime("%H:%M:%S"),
                )
                with self._quotes_lock:
                    self._quotes[code] = quote

            # 清除缓存让下次读取走DB
            self._invalidate_kline_cache(code)

        finally:
            self.unmark_pending(code)

        return results

    # ================================================================
    # 盘中增量刷新 (每60s)
    # ================================================================

    def refresh_quote(self, code: str) -> RealtimeQuote | None:
        """
        增量刷新单只股票：
        1. 调API获取最新日线（start_date=今天）
        2. 更新 _today_bars 和 _quotes
        返回 RealtimeQuote 或 None
        """
        quote = fetch_single_stock_quote(code)
        if quote is None:
            return None

        # 构建今日bar dict
        today_str = datetime.now().strftime("%Y-%m-%d")
        today_bar = {
            "code": code,
            "date": today_str,
            "open": quote.open,
            "high": quote.high,
            "low": quote.low,
            "close": quote.price,
            "volume": quote.volume,
            "period": "daily",
        }

        # 更新内存
        self.update_today_bar(code, "daily", today_bar)
        with self._quotes_lock:
            self._quotes[code] = quote

        return quote

    def refresh_quotes_batch(self, codes: list[str]) -> dict[str, RealtimeQuote]:
        """
        批量增量刷新 (ThreadPool并行)
        返回: {code: RealtimeQuote}
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        results = {}

        if not codes:
            return results

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(self.refresh_quote, c): c for c in codes}
            for future in as_completed(futures):
                code = futures[future]
                try:
                    quote = future.result()
                    if quote:
                        results[code] = quote
                except Exception as e:
                    logger.warning(f"刷新 {code} 失败: {e}")

        return results

    # ================================================================
    # 定期 flush 到 DB
    # ================================================================

    def flush_today_bars(self) -> int:
        """
        将内存中的今日bar批量flush到DB
        返回写入条数
        """
        with self._today_bars_lock:
            bars = list(self._today_bars.values())

        if not bars:
            return 0

        count = save_klines_batch(bars)
        if count > 0:
            logger.debug(f"Flush {count} 条今日bar到DB")
        self._last_flush_time = datetime.now()
        return count

    def should_flush(self, interval_seconds: float = 300.0) -> bool:
        """判断是否需要flush（默认每5分钟）"""
        return (datetime.now() - self._last_flush_time).total_seconds() >= interval_seconds

    def get_pending_codes(self) -> set[str]:
        """获取正在初始获取中的代码集合"""
        with self._pending_lock:
            return set(self._pending_codes)


# 全局单例
_manager: MarketDataManager | None = None
_manager_lock = threading.Lock()


def get_data_manager() -> MarketDataManager:
    """获取全局 MarketDataManager 单例"""
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = MarketDataManager()
    return _manager
