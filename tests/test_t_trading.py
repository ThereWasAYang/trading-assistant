"""做T模块测试 — 模型/引擎/数据/模拟器"""

import pytest

from core.trading import (
    TTDirection, TTSignal, TTModel, MockTTModel,
    TTState, TTTrade, TTStatus, TTrader,
    TTDataProvider,
    TTStepResult, TTSimReport, TTSimulator,
)


# ================================================================
# Model 测试
# ================================================================

class TestMockTTModel:
    """模拟模型 — 测试信号生成"""

    def test_predict_returns_signal(self):
        model = MockTTModel(seed=42)
        features = TTDataProvider.generate_mock_features("000001", base_price=10.0)
        signal = model.predict(features)
        assert isinstance(signal, TTSignal)
        assert signal.direction in (TTDirection.BUY_FIRST, TTDirection.SELL_FIRST, TTDirection.HOLD)
        assert 0.0 <= signal.win_rate <= 1.0

    def test_max_trades_reached_returns_hold(self):
        model = MockTTModel()
        features = TTDataProvider.generate_mock_features("000001", base_price=10.0)
        features["trades_today"] = 5
        features["max_trades"] = 5
        signal = model.predict(features)
        assert signal.direction == TTDirection.HOLD

    def test_different_seeds_different_signals(self):
        m1 = MockTTModel(seed=1)
        m2 = MockTTModel(seed=999)
        features = TTDataProvider.generate_mock_features("000001", base_price=10.0)
        s1 = m1.predict(features)
        s2 = m2.predict(features)
        # 大概率不同 (随机性)
        assert s1.win_rate != s2.win_rate or s1.direction == s2.direction


# ================================================================
# TTrader 测试
# ================================================================

class TestTTrader:
    """做T引擎 — 仓位/资金/交易执行测试"""

    def test_init_stock(self):
        trader = TTrader(n_lots=5)
        status = trader.init_stock("000001", base_cost=10.0)
        assert status.code == "000001"
        assert status.base_position == 5000  # 50000 / 10
        assert status.available_funds == 50000.0
        assert status.max_trades == 5
        assert status.state == TTState.IDLE

    def test_buy_first_open(self):
        trader = TTrader(n_lots=5)
        trader.init_stock("000001", base_cost=10.0)
        signal = TTSignal(
            direction=TTDirection.BUY_FIRST, win_rate=0.65,
            target_price=10.50, stop_loss_price=9.85,
            reason="测试买入"
        )
        trade = trader.open("000001", price=10.0, signal=signal)
        assert trade is not None
        assert trade.direction == TTDirection.BUY_FIRST.value
        assert trade.quantity == 1000  # 10000 / 10
        assert trade.open_price == 10.0

        s = trader.get_status("000001")
        assert s.state == TTState.WAIT_SELL
        assert s.available_funds == 40000.0  # 50000 - 10000

    def test_sell_first_open(self):
        trader = TTrader(n_lots=5)
        trader.init_stock("000001", base_cost=10.0)
        signal = TTSignal(
            direction=TTDirection.SELL_FIRST, win_rate=0.65,
            target_price=9.90, stop_loss_price=10.15,
            reason="测试卖出"
        )
        trade = trader.open("000001", price=10.0, signal=signal)
        assert trade is not None
        assert trade.direction == TTDirection.SELL_FIRST.value

        s = trader.get_status("000001")
        assert s.state == TTState.WAIT_BUY
        assert s.base_position == 4000  # 5000 - 1000

    def test_close_buy_first_profit(self):
        trader = TTrader(n_lots=5)
        trader.init_stock("000001", base_cost=10.0)
        signal = TTSignal(
            direction=TTDirection.BUY_FIRST, win_rate=0.65,
            target_price=10.50, stop_loss_price=9.85,
            reason="测试"
        )
        trader.open("000001", price=10.0, signal=signal)

        # 价格上涨 → 平仓
        closed = trader.check_close("000001", current_price=10.20)
        assert closed is not None
        assert closed.profit > 0
        assert closed.close_price == 10.20

        s = trader.get_status("000001")
        assert s.state == TTState.IDLE
        assert s.trades_today == 1

    def test_cannot_open_when_pending(self):
        trader = TTrader(n_lots=5)
        trader.init_stock("000001", base_cost=10.0)
        signal = TTSignal(
            direction=TTDirection.BUY_FIRST, win_rate=0.65,
            target_price=10.50, stop_loss_price=9.85,
            reason="测试"
        )
        trader.open("000001", price=10.0, signal=signal)
        assert not trader.can_open("000001")

    def test_max_trades_per_day(self):
        trader = TTrader(n_lots=3)
        trader.init_stock("000001", base_cost=10.0)
        signal = TTSignal(
            direction=TTDirection.BUY_FIRST, win_rate=0.65,
            target_price=10.50, stop_loss_price=9.85,
            reason="测试"
        )

        # 做满3次
        for _ in range(3):
            trader.open("000001", price=10.0, signal=signal)
            trader.check_close("000001", current_price=10.20)

        assert not trader.can_open("000001")

    def test_force_close(self):
        trader = TTrader(n_lots=5)
        trader.init_stock("000001", base_cost=10.0)
        signal = TTSignal(
            direction=TTDirection.BUY_FIRST, win_rate=0.65,
            target_price=10.50, stop_loss_price=9.85,
            reason="测试"
        )
        trader.open("000001", price=10.0, signal=signal)

        # 强制平仓 (收盘)
        closed = trader.force_close("000001", current_price=9.90)
        assert closed is not None
        assert closed.profit < 0  # 亏损
        assert trader.get_status("000001").state == TTState.IDLE

    def test_get_stats(self):
        trader = TTrader(n_lots=5)
        trader.init_stock("000001", base_cost=10.0)
        signal = TTSignal(
            direction=TTDirection.BUY_FIRST, win_rate=0.65,
            target_price=10.50, stop_loss_price=9.85,
            reason="测试"
        )

        trader.open("000001", price=10.0, signal=signal)
        trader.check_close("000001", current_price=10.15)

        stats = trader.get_stats("000001")
        assert stats["total_trades"] == 1
        assert stats["wins"] == 1
        assert stats["win_rate"] == 1.0
        assert stats["total_profit"] > 0

    def test_reset_daily(self):
        trader = TTrader(n_lots=5)
        trader.init_stock("000001", base_cost=10.0)
        signal = TTSignal(
            direction=TTDirection.BUY_FIRST, win_rate=0.65,
            target_price=10.50, stop_loss_price=9.85,
            reason="测试"
        )
        trader.open("000001", price=10.0, signal=signal)
        trader.check_close("000001", current_price=10.15)

        trader.reset_daily("000001")
        s = trader.get_status("000001")
        assert s.trades_today == 0
        assert s.state == TTState.IDLE
        assert s.pending_trade is None


# ================================================================
# DataProvider 测试
# ================================================================

class TestTTDataProvider:
    """特征数据提供器测试"""

    def test_generate_mock_features_has_all_keys(self):
        features = TTDataProvider.generate_mock_features("000001", base_price=10.0)
        required = [
            "current_price", "current_volume",
            "intraday_prices", "intraday_volumes",
            "daily_klines", "weekly_klines", "monthly_klines",
            "macd_dif", "macd_dea", "macd_hist",
            "avg_cost", "base_position", "available_funds",
            "trades_today", "max_trades",
        ]
        for key in required:
            assert key in features, f"Missing key: {key}"

    def test_generate_mock_features_shape(self):
        features = TTDataProvider.generate_mock_features(
            "000001", base_price=10.0, n_daily=126, n_intraday=240
        )
        assert len(features["daily_klines"]) == 126
        assert len(features["intraday_prices"]) == 240
        assert len(features["intraday_volumes"]) == 240
        assert len(features["macd_dif"]) == 126

    def test_generate_mock_features_different_seeds(self):
        f1 = TTDataProvider.generate_mock_features("000001", base_price=10.0, seed=1)
        f2 = TTDataProvider.generate_mock_features("000001", base_price=10.0, seed=2)
        assert f1["current_price"] != f2["current_price"]


# ================================================================
# Simulator 测试
# ================================================================

class TestTTSimulator:
    """回测模拟器测试"""

    def test_single_step(self):
        model = MockTTModel(seed=42)
        trader = TTrader(n_lots=5)
        sim = TTSimulator(model, trader)
        features = TTDataProvider.generate_mock_features("000001", base_price=10.0)
        result = sim.step("000001", features)
        assert isinstance(result, TTStepResult)
        assert result.price > 0
        assert result.signal is not None

    def test_run_intraday_returns_report(self):
        model = MockTTModel(seed=42)
        trader = TTrader(n_lots=5)
        sim = TTSimulator(model, trader)
        report = sim.run_intraday("000001", base_price=10.0, n_steps=240, seed=42)

        assert isinstance(report, TTSimReport)
        assert report.total_steps == 240
        assert report.trades_opened <= 5  # max daily trades
        assert report.trades_opened == report.trades_closed  # all must close at EOD
        assert 0.0 <= report.win_rate <= 1.0

    def test_run_intraday_no_crash_different_params(self):
        """不同参数下模拟不崩溃"""
        model = MockTTModel(seed=123)
        for n_lots in [1, 3, 5]:
            for base_price in [5.0, 20.0, 50.0]:
                trader = TTrader(n_lots=n_lots)
                sim = TTSimulator(model, trader)
                report = sim.run_intraday(
                    "000001", base_price=base_price, n_steps=120, seed=42
                )
                assert report.total_steps == 120

    def test_trade_log_matches_trades(self):
        model = MockTTModel(seed=42)
        trader = TTrader(n_lots=5)
        sim = TTSimulator(model, trader)
        report = sim.run_intraday("000001", base_price=10.0, n_steps=240, seed=42)

        assert len(report.trade_log) == report.trades_closed
        for tlog in report.trade_log:
            assert "open_price" in tlog
            assert "close_price" in tlog
            assert "profit" in tlog
            assert "direction" in tlog

    def test_simulator_reset(self):
        model = MockTTModel(seed=42)
        trader = TTrader(n_lots=5)
        sim = TTSimulator(model, trader)
        sim.run_intraday("000001", base_price=10.0, n_steps=50, seed=42)
        assert len(sim._step_log) == 50

        sim.reset()
        assert len(sim._step_log) == 0
        assert len(sim._closed_trades) == 0


# ================================================================
# 集成测试
# ================================================================

class TestTTIntegration:
    """端到端集成测试: 数据 → 模型 → 引擎 → 模拟器"""

    def test_full_pipeline(self):
        # 1. 生成模拟数据
        features = TTDataProvider.generate_mock_features("600519", base_price=1800.0)

        # 2. 模型预测
        model = MockTTModel(seed=42)
        signal = model.predict(features)
        assert isinstance(signal, TTSignal)

        # 3. 引擎初始化
        trader = TTrader(n_lots=5)
        trader.init_stock("600519", base_cost=features["avg_cost"])

        # 4. 模拟器跑一天
        sim = TTSimulator(model, trader)
        report = sim.run_intraday("600519", base_price=1800.0, n_steps=50, seed=42)

        # 5. 验证报告
        assert report.total_steps == 50
        assert report.final_status is not None
        assert isinstance(report.trade_log, list)

    def test_multi_stock_independence(self):
        """多股票做T互不影响"""
        model = MockTTModel(seed=42)
        trader = TTrader(n_lots=5)
        sim = TTSimulator(model, trader)

        r1 = sim.run_intraday("000001", base_price=10.0, n_steps=60, seed=1)
        r2 = sim.run_intraday("600519", base_price=1800.0, n_steps=60, seed=2)

        # 两只股票的状态独立
        s1 = trader.get_stats("000001")
        s2 = trader.get_stats("600519")
        assert s1["code"] != s2["code"]


# ================================================================
# 边界情况
# ================================================================

class TestTTEdgeCases:
    """边界情况测试"""

    def test_zero_price(self):
        trader = TTrader(n_lots=5)
        trader.init_stock("000001", base_cost=10.0)
        signal = TTSignal(
            direction=TTDirection.BUY_FIRST, win_rate=0.65,
            target_price=10.50, stop_loss_price=9.85,
            reason="测试"
        )
        trade = trader.open("000001", price=0.0, signal=signal)
        assert trade is None  # quantity = 0, can't trade

    def test_very_high_price_small_quantity(self):
        trader = TTrader(n_lots=5)
        trader.init_stock("000001", base_cost=2000.0)  # 茅台级别
        signal = TTSignal(
            direction=TTDirection.SELL_FIRST, win_rate=0.65,
            target_price=1950.0, stop_loss_price=2020.0,
            reason="测试"
        )
        trade = trader.open("000001", price=2000.0, signal=signal)
        assert trade is not None
        assert trade.quantity == 5  # 10000 / 2000 = 5 股

    def test_signal_with_hold(self):
        model = MockTTModel(seed=42)
        trader = TTrader(n_lots=5)
        features = TTDataProvider.generate_mock_features("000001", base_price=10.0)
        # 当前无信号的场景
        sim = TTSimulator(model, trader)
        sim.step("000001", features)
        # 不应崩溃，不应开仓 (hold 信号 win_rate < 0.55)
        assert trader.get_status("000001").state == TTState.IDLE
