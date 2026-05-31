"""AKShare数据接口封装 - 异步获取通过QThread + pyqtSignal，集成缓存防封IP"""

import pandas as pd
from datetime import datetime, timedelta
from typing import Optional

from PyQt5.QtCore import QThread, pyqtSignal

from data.models import RealtimeQuote, KLineData
from utils.logger import get_logger
from utils.cache import (
    get_quotes_cache, get_kline_cache, get_search_cache, get_30min_cache,
)

logger = get_logger(__name__)


# ============================================================
# 数据安全转换工具
# ============================================================

def _safe_float(val, default=0.0) -> float:
    try:
        return float(val) if pd.notna(val) else default
    except (ValueError, TypeError):
        return default


def _safe_int(val, default=0) -> int:
    try:
        return int(float(val)) if pd.notna(val) else default
    except (ValueError, TypeError):
        return default


# ============================================================
# 同步数据获取 (在工作线程中调用)
# ============================================================

def fetch_realtime_quotes(codes: Optional[list[str]] = None) -> dict[str, RealtimeQuote]:
    """
    获取A股实时行情 (批量，带缓存)
    使用 AKShare stock_zh_a_spot_em() 获取全市场数据后过滤
    返回: {code: RealtimeQuote}
    """
    cache = get_quotes_cache()
    cache_key = "all_quotes"

    # 尝试从缓存获取
    cached_result = cache.get(cache_key)
    if cached_result is not None:
        if codes:
            return {c: cached_result[c] for c in codes if c in cached_result}
        return cached_result

    try:
        import akshare as ak
        logger.debug("从AKShare获取全市场实时行情...")
        df = ak.stock_zh_a_spot_em()

        # 标准化列名
        col_map = {
            "代码": "code", "名称": "name", "最新价": "price",
            "涨跌幅": "change_pct", "涨跌额": "change_amt",
            "成交量": "volume", "成交额": "turnover",
            "最高": "high", "最低": "low",
            "今开": "open", "昨收": "pre_close",
        }
        df = df.rename(columns=col_map)
        needed = list(col_map.values())
        df = df[[c for c in needed if c in df.columns]]

        result = {}
        for _, row in df.iterrows():
            code = str(row.get("code", ""))
            if not code:
                continue
            result[code] = RealtimeQuote(
                code=code,
                name=str(row.get("name", "")),
                price=_safe_float(row.get("price")),
                change_pct=_safe_float(row.get("change_pct")),
                change_amt=_safe_float(row.get("change_amt")),
                volume=_safe_int(row.get("volume")),
                turnover=_safe_float(row.get("turnover")),
                high=_safe_float(row.get("high")),
                low=_safe_float(row.get("low")),
                open=_safe_float(row.get("open")),
                pre_close=_safe_float(row.get("pre_close")),
                timestamp=datetime.now().strftime("%H:%M:%S"),
            )

        # 缓存全市场数据
        cache.set(cache_key, result, ttl=5.0)
        logger.info(f"获取到 {len(result)} 只股票实时行情，缓存5秒")
        return result

    except Exception as e:
        logger.error(f"获取实时行情失败: {e}")
        return {}


def fetch_kline(
    code: str,
    period: str = "daily",
    days: int = 250,
) -> list[KLineData]:
    """
    获取历史K线数据 (带缓存)
    period: 'daily' | 'weekly' | 'monthly'
    返回: list[KLineData] (按日期升序)
    """
    cache = get_kline_cache()
    cache_key = f"kline:{code}:{period}:{days}"

    cached = cache.get(cache_key)
    if cached is not None:
        logger.debug(f"K线缓存命中: {code} {period}")
        return cached

    try:
        import akshare as ak
        logger.debug(f"从AKShare获取K线: {code} {period}")

        if period == "daily":
            df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
        elif period == "weekly":
            df = ak.stock_zh_a_hist(symbol=code, period="weekly", adjust="qfq")
        elif period == "monthly":
            df = ak.stock_zh_a_hist(symbol=code, period="monthly", adjust="qfq")
        else:
            logger.warning(f"不支持的K线周期: {period}")
            return []

        if df is None or df.empty:
            logger.warning(f"{code} {period} K线数据为空")
            return []

        col_map = {
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
        }
        df = df.rename(columns=col_map)

        if len(df) > days:
            df = df.tail(days)

        result = []
        for _, row in df.iterrows():
            result.append(KLineData(
                code=code,
                date=str(row["date"])[:10],
                open=_safe_float(row.get("open")),
                high=_safe_float(row.get("high")),
                low=_safe_float(row.get("low")),
                close=_safe_float(row.get("close")),
                volume=_safe_int(row.get("volume")),
                period=period,
            ))

        cache.set(cache_key, result, ttl=300.0)  # K线缓存5分钟
        logger.info(f"获取 {code} {period} K线 {len(result)} 条，缓存5分钟")
        return result

    except Exception as e:
        logger.error(f"获取K线失败 ({code}, {period}): {e}")
        return []


def fetch_30min_kline(code: str, days: int = 60) -> list[KLineData]:
    """
    获取短周期K线数据 (用于30分钟级别分析)
    注意: AKShare分钟接口 period='60' 获取60分钟线，
    用60分钟线近似30分钟级别分析（每日8根60min线 ≈ 16根30min线）
    返回: list[KLineData]
    """
    cache = get_30min_cache()
    cache_key = f"kline_60min:{code}:{days}"

    cached = cache.get(cache_key)
    if cached is not None:
        logger.debug(f"60min K线缓存命中: {code}")
        return cached

    try:
        import akshare as ak
        logger.debug(f"从AKShare获取60分钟K线: {code} (近似30分钟级别)")

        df = ak.stock_zh_a_hist_min_em(symbol=code, period="60", adjust="qfq")

        if df is None or df.empty:
            logger.warning(f"{code} 分钟K线数据为空")
            return []

        col_map = {
            "时间": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
        }
        df = df.rename(columns=col_map)

        limit = days * 8  # 每天约8根60分钟K线
        if len(df) > limit:
            df = df.tail(limit)

        result = []
        for _, row in df.iterrows():
            result.append(KLineData(
                code=code,
                date=str(row["date"]),
                open=_safe_float(row.get("open")),
                high=_safe_float(row.get("high")),
                low=_safe_float(row.get("low")),
                close=_safe_float(row.get("close")),
                volume=_safe_int(row.get("volume")),
                period="60min",
            ))

        cache.set(cache_key, result, ttl=120.0)  # 短周期缓存2分钟
        logger.info(f"获取 {code} 60min K线 {len(result)} 条")
        return result

    except Exception as e:
        logger.error(f"获取分钟K线失败 ({code}): {e}")
        return []


def fetch_intraday_data(code: str) -> list[dict]:
    """
    获取当日分时数据 (缓存1分钟)
    返回: [{time, price, volume, avg_price}, ...]
    """
    cache = get_30min_cache()
    cache_key = f"intraday:{code}"

    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        import akshare as ak
        logger.debug(f"获取分时数据: {code}")

        df = ak.stock_zh_a_minute(symbol=code, period="1")

        if df is None or df.empty:
            return []

        result = []
        cum_vol = 0
        cum_amt = 0.0
        for _, row in df.iterrows():
            price = _safe_float(row.get("收盘"))
            vol = _safe_int(row.get("成交量"))
            cum_vol += vol
            cum_amt += price * vol
            avg_price = cum_amt / cum_vol if cum_vol > 0 else price

            result.append({
                "time": str(row.get("时间", "")) if "时间" in df.columns else str(row.name),
                "price": price,
                "volume": vol,
                "avg_price": round(avg_price, 2),
            })

        cache.set(cache_key, result, ttl=60.0)
        return result

    except Exception as e:
        logger.error(f"获取分时数据失败 ({code}): {e}")
        return []


def search_stock(keyword: str) -> list[dict]:
    """搜索股票代码或名称 (带缓存)"""
    cache = get_search_cache()

    cached = cache.get("all_stocks")
    if cached is None:
        try:
            import akshare as ak
            logger.debug("从AKShare加载全市场股票列表...")
            df = ak.stock_zh_a_spot_em()
            results = []
            for _, row in df.iterrows():
                results.append({
                    "code": str(row["代码"]),
                    "name": str(row["名称"]),
                    "price": _safe_float(row.get("最新价")),
                })
            cache.set("all_stocks", results, ttl=600.0)  # 全市场列表缓存10分钟
            logger.info(f"全市场股票列表已缓存: {len(results)} 只")
        except Exception as e:
            logger.error(f"搜索股票失败: {e}")
            return []
    else:
        results = cached

    # 按代码或名称模糊搜索
    keyword_lower = keyword.lower()
    filtered = [
        r for r in results
        if keyword_lower in r["code"].lower() or keyword_lower in r["name"].lower()
    ]
    return filtered[:20]


# ============================================================
# 异步数据工作线程
# ============================================================

class RealtimeWorker(QThread):
    """实时行情获取工作线程"""
    data_ready = pyqtSignal(dict)  # {code: RealtimeQuote}
    error_occurred = pyqtSignal(str)

    def __init__(self, codes: list[str], parent=None):
        super().__init__(parent)
        self.codes = codes

    def run(self):
        try:
            result = fetch_realtime_quotes(self.codes)
            self.data_ready.emit(result)
        except Exception as e:
            logger.error(f"RealtimeWorker异常: {e}")
            self.error_occurred.emit(str(e))


class KLineWorker(QThread):
    """K线数据获取工作线程"""
    data_ready = pyqtSignal(str, str, list)  # (code, period, list[KLineData])
    error_occurred = pyqtSignal(str)

    def __init__(self, code: str, period: str = "daily", days: int = 250, parent=None):
        super().__init__(parent)
        self.code = code
        self.period = period
        self.days = days

    def run(self):
        try:
            if self.period == "30min":
                result = fetch_30min_kline(self.code, self.days)
            else:
                result = fetch_kline(self.code, self.period, self.days)
            self.data_ready.emit(self.code, self.period, result)
        except Exception as e:
            logger.error(f"KLineWorker异常: {e}")
            self.error_occurred.emit(str(e))


class IntradayWorker(QThread):
    """分时数据获取工作线程"""
    data_ready = pyqtSignal(str, list)  # (code, [{time, price, volume, avg_price}])
    error_occurred = pyqtSignal(str)

    def __init__(self, code: str, parent=None):
        super().__init__(parent)
        self.code = code

    def run(self):
        try:
            result = fetch_intraday_data(self.code)
            self.data_ready.emit(self.code, result)
        except Exception as e:
            logger.error(f"IntradayWorker异常: {e}")
            self.error_occurred.emit(str(e))


class StockSearchWorker(QThread):
    """股票搜索工作线程"""
    data_ready = pyqtSignal(list)  # [{code, name, price}]
    error_occurred = pyqtSignal(str)

    def __init__(self, keyword: str, parent=None):
        super().__init__(parent)
        self.keyword = keyword

    def run(self):
        try:
            result = search_stock(self.keyword)
            self.data_ready.emit(result)
        except Exception as e:
            logger.error(f"StockSearchWorker异常: {e}")
            self.error_occurred.emit(str(e))
