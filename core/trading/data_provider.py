"""做T数据提供器 — 从 MarketDataManager 汇集模型所需特征数据"""

from __future__ import annotations

from datetime import datetime

from data.models import KLineData, RealtimeQuote
from core.technical import calc_ema, kline_to_arrays


class TTDataProvider:
    """做T特征数据提供器

    从 MarketDataManager 获取各维度数据，组装为模型输入 features dict。
    支持真实数据和模拟数据两种模式。
    """

    def __init__(self, data_manager=None):
        """
        data_manager: MarketDataManager 实例 (真实模式) 或 None (模拟模式)
        """
        self._manager = data_manager

    def gather_features(
        self,
        code: str,
        quote: RealtimeQuote | None = None,
        avg_cost: float = 0.0,
        base_position: int = 0,
        available_funds: float = 0.0,
        trades_today: int = 0,
        max_trades: int = 5,
    ) -> dict:
        """
        从 Manager 汇集模型所需全部特征数据

        返回完整的 features dict，可直接传给 model.predict()
        """
        features = {
            # ---- 价格/成交量 ----
            "current_price": quote.price if quote else 0.0,
            "current_volume": quote.volume if quote else 0,

            # ---- 日内数据 ----
            "intraday_prices": [],
            "intraday_volumes": [],

            # ---- 历史K线 ----
            "daily_klines": [],
            "weekly_klines": [],
            "monthly_klines": [],

            # ---- MACD 指标 ----
            "macd_dif": [],
            "macd_dea": [],
            "macd_hist": [],

            # ---- 持仓信息 ----
            "avg_cost": avg_cost,
            "base_position": base_position,
            "available_funds": available_funds,
            "trades_today": trades_today,
            "max_trades": max_trades,
        }

        if self._manager is None:
            return features

        # 从 Manager 加载日线
        daily = self._manager.get_klines(code, "daily", days=126)
        if daily:
            features["daily_klines"] = daily
            # 计算 MACD
            arr = kline_to_arrays(daily)
            if len(arr["closes"]) >= 26:
                dif, dea, hist = self._calc_macd(arr["closes"])
                features["macd_dif"] = [round(v, 4) for v in dif]
                features["macd_dea"] = [round(v, 4) for v in dea]
                features["macd_hist"] = [round(v, 4) for v in hist]

            # 日内数据: 从今日bar提取
            today = daily[-1]
            if today.date == datetime.now().strftime("%Y-%m-%d"):
                features["intraday_prices"] = [today.open, today.low, today.high, today.close]
                features["intraday_volumes"] = [today.volume]

        # 周线/月线
        weekly = self._manager.get_klines(code, "weekly", days=52)
        if weekly:
            features["weekly_klines"] = weekly

        monthly = self._manager.get_klines(code, "monthly", days=12)
        if monthly:
            features["monthly_klines"] = monthly

        return features

    @staticmethod
    def _calc_macd(closes: list[float],
                   fast: int = 12, slow: int = 26, signal: int = 9):
        """计算 MACD 指标 → (DIF, DEA, MACD柱)"""
        import numpy as np
        closes_arr = np.array(closes, dtype=float)
        ema_fast = calc_ema(closes_arr, fast)
        ema_slow = calc_ema(closes_arr, slow)
        dif = ema_fast - ema_slow
        dea = calc_ema(np.nan_to_num(dif), signal)
        hist = 2 * (dif - dea)
        return dif.tolist(), dea.tolist(), hist.tolist()

    # ================================================================
    # 模拟数据生成 (测试用)
    # ================================================================

    @staticmethod
    def generate_mock_features(
        code: str = "000001",
        base_price: float = 10.0,
        n_daily: int = 126,
        n_intraday: int = 240,
        seed: int = 42,
    ) -> dict:
        """生成模拟特征数据 — 含真实结构的随机数据，用于测试模型接口"""
        import random as _random
        import numpy as np
        rng = _random.Random(seed)
        np_rng = np.random.RandomState(seed)

        # 日线: 随机游走
        prices = [base_price]
        for _ in range(n_daily - 1):
            change = rng.gauss(0, base_price * 0.02)
            prices.append(max(prices[-1] + change, base_price * 0.5))

        daily_klines = []
        for i, close in enumerate(prices):
            daily = rng.uniform(close * 0.95, close * 1.05)
            high = max(close, daily) * (1 + abs(rng.gauss(0, 0.01)))
            low = min(close, daily) * (1 - abs(rng.gauss(0, 0.01)))
            daily_klines.append(KLineData(
                code=code,
                date=f"2026-{(i // 22 + 1):02d}-{(i % 22 + 1):02d}",
                open=round(daily, 2),
                high=round(high, 2),
                low=round(low, 2),
                close=round(close, 2),
                volume=int(rng.uniform(50000, 500000)),
                period="daily",
            ))

        # 周线: 聚合
        weekly_klines = daily_klines[::5][-52:]

        # 月线: 聚合
        monthly_klines = daily_klines[::22][-12:]

        # 日内数据: 当日随机游走
        intraday_prices = [base_price]
        for _ in range(n_intraday - 1):
            change = rng.gauss(0, base_price * 0.001)
            intraday_prices.append(max(intraday_prices[-1] + change, base_price * 0.95))
        intraday_volumes = [int(rng.uniform(1000, 50000)) for _ in range(n_intraday)]

        current_price = intraday_prices[-1]

        # MACD: 用日线计算
        closes_arr = np.array([k.close for k in daily_klines], dtype=float)
        ema_fast = calc_ema(closes_arr, 12)
        ema_slow = calc_ema(closes_arr, 26)
        dif = ema_fast - ema_slow
        dea = calc_ema(np.nan_to_num(dif), 9)
        hist = 2 * (dif - dea)

        return {
            "current_price": round(current_price, 2),
            "current_volume": intraday_volumes[-1],
            "intraday_prices": [round(p, 2) for p in intraday_prices],
            "intraday_volumes": intraday_volumes,
            "daily_klines": daily_klines,
            "weekly_klines": weekly_klines,
            "monthly_klines": monthly_klines,
            "macd_dif": [round(float(v), 4) for v in dif],
            "macd_dea": [round(float(v), 4) for v in dea],
            "macd_hist": [round(float(v), 4) for v in hist],
            "avg_cost": round(base_price * 0.98, 2),
            "base_position": int(50000 / base_price),
            "available_funds": 50000.0,
            "trades_today": 0,
            "max_trades": 5,
        }
