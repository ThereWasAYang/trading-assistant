"""做T模拟器 — 回测引擎 + 测试接口

用法:
    model = MockTTModel()
    trader = TTrader(n_lots=5)
    sim = TTSimulator(model, trader)

    # 单步模拟
    features = TTDataProvider.generate_mock_features()
    result = sim.step("000001", features)

    # 连续模拟 (日内240根分钟线)
    report = sim.run_intraday("000001", base_price=10.0, n_steps=240)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from core.trading.model import TTSignal, TTDirection, TTModel, MockTTModel
from core.trading.t_trader import TTrader, TTTrade, TTStatus, TTState
from core.trading.data_provider import TTDataProvider


@dataclass
class TTStepResult:
    """单步模拟结果"""
    step: int = 0
    time: str = ""
    price: float = 0.0
    signal: TTSignal | None = None
    opened: TTTrade | None = None
    closed: TTTrade | None = None
    status: dict | None = None  # TTStatus 快照


@dataclass
class TTSimReport:
    """模拟报告"""
    code: str = ""
    total_steps: int = 0
    signals_generated: int = 0        # 产生信号的次数
    trades_opened: int = 0            # 开仓次数
    trades_closed: int = 0            # 平仓次数
    total_profit: float = 0.0         # 总收益
    win_trades: int = 0
    loss_trades: int = 0
    win_rate: float = 0.0             # 胜率
    avg_profit_per_trade: float = 0.0
    final_status: dict | None = None  # 最终状态快照
    trade_log: list[dict] = field(default_factory=list)  # 每笔交易详情


class TTSimulator:
    """做T模拟器 — 用于模型回测和验证"""

    def __init__(self, model: TTModel | None = None, trader: TTrader | None = None):
        self.model = model or MockTTModel()
        self.trader = trader or TTrader(n_lots=5)
        self.data_provider = TTDataProvider()  # 模拟模式 (无 Manager)
        self._step_log: list[TTStepResult] = []
        self._closed_trades: list[TTTrade] = []

    # ================================================================
    # 单步模拟
    # ================================================================

    def step(self, code: str, features: dict) -> TTStepResult:
        """
        单步模拟: 特征 → 模型预测 → 交易决策

        返回 TTStepResult 包含本步的完整状态
        """
        price = features.get("current_price", 0.0)
        step_result = TTStepResult(
            step=len(self._step_log),
            time=datetime.now().strftime("%H:%M:%S"),
            price=price,
        )

        # 确保股票已初始化
        s = self.trader.get_or_init(code, features.get("avg_cost", price))

        # 先检查是否需要平仓 (有pending交易时)
        signal = self.model.predict(features)
        step_result.signal = signal

        if s.state != TTState.IDLE and s.pending_trade:
            closed = self.trader.check_close(code, price, signal)
            if closed:
                step_result.closed = closed
                self._closed_trades.append(closed)

        # 再检查是否可以开新仓
        if self.trader.can_open(code) and signal.direction != TTDirection.HOLD:
            if signal.win_rate >= 0.55:  # 胜率阈值
                opened = self.trader.open(code, price, signal)
                if opened:
                    step_result.opened = opened

        step_result.status = self.trader.get_stats(code)
        self._step_log.append(step_result)
        return step_result

    # ================================================================
    # 连续模拟
    # ================================================================

    def run_intraday(
        self,
        code: str = "000001",
        base_price: float = 10.0,
        n_steps: int = 240,
        seed: int = 42,
    ) -> TTSimReport:
        """
        模拟一个交易日 (240根分钟线)

        每步:
          1. 生成当步特征 (价格随时间变化)
          2. 模型预测 → 交易决策

        返回完整的 TTSimReport
        """
        import random as _random
        rng = _random.Random(seed)

        self._step_log = []
        self._closed_trades = []

        # 初始化特征
        features = TTDataProvider.generate_mock_features(
            code=code, base_price=base_price, n_intraday=n_steps, seed=seed,
        )
        self.trader.init_stock(code, features["avg_cost"])

        price = base_price
        for i in range(n_steps):
            # 模拟价格波动 (随机游走 + 均值回归)
            price = price * (1 + rng.gauss(0, 0.002))
            price = max(price, base_price * 0.92)
            price = min(price, base_price * 1.08)

            # 更新当前步特征
            features["current_price"] = round(price, 2)
            features["current_volume"] = features["intraday_volumes"][i]
            features["trades_today"] = self.trader.get_status(code).trades_today
            features["base_position"] = self.trader.get_status(code).base_position
            features["available_funds"] = self.trader.get_status(code).available_funds

            self.step(code, features)

        # 收盘强制平仓
        code_status = self.trader.get_status(code)
        if code_status and code_status.state != TTState.IDLE:
            self.trader.force_close(code, price)

        return self._build_report(code)

    # ================================================================
    # 报告
    # ================================================================

    def _build_report(self, code: str) -> TTSimReport:
        """从日志构建模拟报告"""
        opened_count = sum(1 for s in self._step_log if s.opened)
        closed_count = sum(1 for s in self._step_log if s.closed)
        signal_count = sum(
            1 for s in self._step_log
            if s.signal and s.signal.direction != TTDirection.HOLD
        )

        trades = self._closed_trades
        wins = [t for t in trades if t.profit > 0]
        losses = [t for t in trades if t.profit <= 0]

        return TTSimReport(
            code=code,
            total_steps=len(self._step_log),
            signals_generated=signal_count,
            trades_opened=opened_count,
            trades_closed=closed_count,
            total_profit=round(sum(t.profit for t in trades), 2),
            win_trades=len(wins),
            loss_trades=len(losses),
            win_rate=round(len(wins) / len(trades), 3) if trades else 0.0,
            avg_profit_per_trade=round(
                sum(t.profit for t in trades) / len(trades), 2
            ) if trades else 0.0,
            final_status=self.trader.get_stats(code),
            trade_log=[{
                "open": t.open_time,
                "close": t.close_time,
                "direction": t.direction,
                "open_price": t.open_price,
                "close_price": t.close_price,
                "quantity": t.quantity,
                "profit": round(t.profit, 2),
                "profit_pct": t.profit_pct,
                "reason": t.reason,
            } for t in trades],
        )

    def reset(self):
        """重置模拟器"""
        self._step_log = []
        self._closed_trades = []
        self.trader = TTrader(n_lots=self.trader.n_lots)
