"""日内做T模块

核心组件:
  TTModel     — 模型接口 (实现 predict(features) -> TTSignal)
  TTrader     — 做T引擎 (仓位/资金/交易执行)
  TTDataProvider — 特征数据提供器
  TTSimulator — 模拟回测引擎

用法:
  from core.trading import TTrader, TTSimulator, MockTTModel

  model = MockTTModel()
  trader = TTrader(n_lots=5)
  sim = TTSimulator(model, trader)
  report = sim.run_intraday("000001", base_price=10.0, n_steps=240)
  print(f"胜率: {report.win_rate:.1%}, 收益: {report.total_profit:+.2f}")
"""

from core.trading.model import (
    TTDirection, TTSignal, TTModel, MockTTModel,
)
from core.trading.t_trader import (
    TTState, TTTrade, TTStatus, TTrader,
)
from core.trading.data_provider import TTDataProvider
from core.trading.simulator import (
    TTStepResult, TTSimReport, TTSimulator,
)

__all__ = [
    "TTDirection", "TTSignal", "TTModel", "MockTTModel",
    "TTState", "TTTrade", "TTStatus", "TTrader",
    "TTDataProvider",
    "TTStepResult", "TTSimReport", "TTSimulator",
]
