"""做T模型接口 — 抽象基类 + 模拟实现

模型输入 (features dict):
    current_price: float           # 当前价
    current_volume: int            # 当前成交量
    intraday_prices: list[float]   # 今日全部分钟价格
    intraday_volumes: list[int]    # 今日全部分钟成交量
    daily_klines: list[KLineData]  # 最近半年日线
    weekly_klines: list[KLineData] # 最近半年周线
    monthly_klines: list[KLineData]# 最近半年月线
    macd_dif: list[float]          # 日线MACD DIF
    macd_dea: list[float]          # 日线MACD DEA
    macd_hist: list[float]         # 日线MACD 柱
    avg_cost: float                # 底仓均价
    base_position: int             # 底仓股数
    available_funds: float         # 可用资金
    trades_today: int              # 今日已做T次数
    max_trades: int                # 每日最大做T次数

模型输出:
    predict(features) -> TTSignal  # 做T信号 (方向 + 胜率)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class TTDirection(str, Enum):
    BUY_FIRST = "buy_first"    # 先买后卖 (低买高卖)
    SELL_FIRST = "sell_first"  # 先卖后回补 (高卖低买)
    HOLD = "hold"              # 不做


@dataclass
class TTSignal:
    """做T信号"""
    direction: TTDirection = TTDirection.HOLD
    win_rate: float = 0.0         # 预测胜率 0.0-1.0
    target_price: float = 0.0     # 目标价格
    stop_loss_price: float = 0.0  # 止损价
    confidence: float = 0.0       # 置信度 0.0-1.0
    reason: str = ""              # 信号原因


class TTModel(ABC):
    """做T模型抽象基类 — 所有模型需实现 predict 方法"""

    @abstractmethod
    def predict(self, features: dict) -> TTSignal:
        """输入特征 → 输出做T信号 (方向 + 胜率)"""
        ...

    @abstractmethod
    def name(self) -> str:
        """模型名称"""
        ...


# ============================================================
# 模拟模型 (用于测试框架)
# ============================================================

class MockTTModel(TTModel):
    """模拟做T模型 — 基于简单规则的随机信号，仅用于测试框架"""

    def __init__(self, seed: int = 42, win_rate_base: float = 0.55):
        import random as _random
        self._rng = _random.Random(seed)
        self._win_rate_base = win_rate_base

    def name(self) -> str:
        return "MockTTModel"

    def predict(self, features: dict) -> TTSignal:
        """基于简单规则生成模拟信号"""
        price = features.get("current_price", 10.0)
        avg_cost = features.get("avg_cost", price)
        trades_today = features.get("trades_today", 0)
        max_trades = features.get("max_trades", 5)
        intraday_prices = features.get("intraday_prices", [])
        daily_klines = features.get("daily_klines", [])

        # 已达上限 → HOLD
        if trades_today >= max_trades:
            return TTSignal(direction=TTDirection.HOLD, win_rate=0.0,
                            reason="已达每日最大做T次数")

        # 日内价格偏离均价2%以上 → 做T机会
        if intraday_prices and len(intraday_prices) > 30:
            avg_today = sum(intraday_prices[-30:]) / 30
            deviation = (price - avg_today) / avg_today

            if deviation > 0.02:
                # 当前价高于日内均价 → 先卖后买
                wr = self._rng.uniform(self._win_rate_base, self._win_rate_base + 0.15)
                return TTSignal(
                    direction=TTDirection.SELL_FIRST,
                    win_rate=round(wr, 3),
                    target_price=round(avg_today, 2),
                    stop_loss_price=round(price * 1.015, 2),
                    confidence=round(wr - 0.45, 3),
                    reason=f"高于日内均价{deviation:.1%}，先卖后买"
                )
            elif deviation < -0.02:
                # 当前价低于日内均价 → 先买后卖
                wr = self._rng.uniform(self._win_rate_base, self._win_rate_base + 0.15)
                return TTSignal(
                    direction=TTDirection.BUY_FIRST,
                    win_rate=round(wr, 3),
                    target_price=round(avg_today, 2),
                    stop_loss_price=round(price * 0.985, 2),
                    confidence=round(wr - 0.45, 3),
                    reason=f"低于日内均价{abs(deviation):.1%}，先买后卖"
                )

        # 基于日线MACD趋势判断
        macd_hist = features.get("macd_hist", [])
        if macd_hist and len(macd_hist) >= 3:
            if macd_hist[-1] > macd_hist[-2] > macd_hist[-3] and macd_hist[-1] > 0:
                # MACD柱连续放大且为正 → 看涨，先买后卖
                wr = self._rng.uniform(self._win_rate_base, self._win_rate_base + 0.1)
                return TTSignal(
                    direction=TTDirection.BUY_FIRST,
                    win_rate=round(wr, 3),
                    target_price=round(price * 1.01, 2),
                    stop_loss_price=round(price * 0.99, 2),
                    confidence=round(wr - 0.48, 3),
                    reason="MACD柱连续放大, 看涨"
                )

        # 默认: 弱信号 → HOLD
        wr = self._rng.uniform(0.35, 0.50)
        return TTSignal(
            direction=TTDirection.HOLD,
            win_rate=round(wr, 3),
            confidence=round(wr - 0.45, 3),
            reason="无明确做T信号"
        )
