"""K线/分时图组件 — mplfinance + matplotlib 嵌入 PyQt5"""

import numpy as np
import pandas as pd
from datetime import datetime

from PyQt5.QtWidgets import QWidget, QVBoxLayout, QTabWidget
from PyQt5.QtCore import pyqtSignal

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
import mplfinance as mpf
import matplotlib.pyplot as plt

from config import CHART_STYLE, MA_PERIODS, CHART_COLORS
from data.market_data import KLineWorker, IntradayWorker
from data.models import KLineData


# 设置中文字体
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# mplfinance 样式
mpf_color = mpf.make_marketcolors(
    up=CHART_COLORS["up"],
    down=CHART_COLORS["down"],
    edge="inherit",
    wick="inherit",
    volume={"up": CHART_COLORS["volume_up"], "down": CHART_COLORS["volume_down"]},
)
mpf_style = mpf.make_mpf_style(
    marketcolors=mpf_color,
    gridstyle="--",
    y_on_right=False,
)


class MplCanvas(FigureCanvas):
    """Matplotlib 画布"""

    def __init__(self, figsize=(10, 6), dpi=100):
        self.fig = Figure(figsize=figsize, dpi=dpi, tight_layout=True)
        super().__init__(self.fig)


class ChartTabWidget(QWidget):
    """单个图表页 (K线图)"""

    def __init__(self, period: str = "daily", parent=None):
        super().__init__(parent)
        self.period = period  # 'intraday', 'daily', 'weekly', 'monthly'
        self.code = ""
        self.klines: list[KLineData] = []
        self.intraday_data: list[dict] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.canvas = MplCanvas()
        self.toolbar = NavigationToolbar(self.canvas, self)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas)

    def load_data(self, code: str):
        """加载指定股票的图表数据"""
        self.code = code
        if self.period == "intraday":
            self._load_intraday()
        else:
            self._load_kline()

    def _load_intraday(self):
        """加载分时数据"""
        self.worker = IntradayWorker(self.code)
        self.worker.data_ready.connect(self._on_intraday_ready)
        self.worker.start()

    def _on_intraday_ready(self, code: str, data: list[dict]):
        """分时数据到达"""
        if code != self.code:
            return
        self.intraday_data = data
        self._draw_intraday()

    def _load_kline(self):
        """加载K线数据"""
        days_map = {"daily": 250, "weekly": 100, "monthly": 60}
        days = days_map.get(self.period, 250)
        self.worker = KLineWorker(self.code, self.period, days)
        self.worker.data_ready.connect(self._on_kline_ready)
        self.worker.start()

    def _on_kline_ready(self, code: str, period: str, klines: list[KLineData]):
        """K线数据到达"""
        if code != self.code or period != self.period:
            return
        self.klines = klines
        self._draw_kline()

    # ================================================================
    # 图表绘制
    # ================================================================

    def _draw_intraday(self):
        """绘制分时图"""
        self.canvas.fig.clear()

        if not self.intraday_data:
            self.canvas.draw()
            return

        times = [d["time"] for d in self.intraday_data]
        prices = [d["price"] for d in self.intraday_data]
        avg_prices = [d["avg_price"] for d in self.intraday_data]
        volumes = [d["volume"] for d in self.intraday_data]

        # 上栏: 价格走势
        ax1 = self.canvas.fig.add_subplot(2, 1, 1)
        ax1.plot(range(len(prices)), prices, color="#333333", linewidth=1.0, label="价格")
        ax1.plot(range(len(avg_prices)), avg_prices, color="#FFA500",
                 linewidth=0.8, linestyle="--", label="均价")

        # 标注昨收
        if self.klines:
            pre_close = self.klines[-1].close
        elif prices:
            pre_close = prices[0]
        else:
            pre_close = 0
        if pre_close > 0:
            ax1.axhline(y=pre_close, color="#999999", linewidth=0.5, linestyle="-.")

        ax1.set_ylabel("价格")
        ax1.legend(loc="upper left", fontsize=8)
        ax1.grid(True, alpha=0.3)

        # 设置x轴标签
        step = max(1, len(times) // 8)
        tick_positions = list(range(0, len(times), step))
        tick_labels = [times[i] for i in tick_positions if i < len(times)]
        ax1.set_xticks(tick_positions[:len(tick_labels)])
        ax1.set_xticklabels(tick_labels, rotation=30, fontsize=7)

        # 下栏: 成交量
        ax2 = self.canvas.fig.add_subplot(2, 1, 2)
        colors_vol = ["#DC143C" if i > 0 and prices[i] >= prices[i - 1]
                      else "#008000" for i in range(len(prices))]
        ax2.bar(range(len(volumes)), volumes, color=colors_vol, width=1.0)
        ax2.set_ylabel("成交量")
        ax2.set_xticks(tick_positions[:len(tick_labels)])
        ax2.set_xticklabels(tick_labels, rotation=30, fontsize=7)
        ax2.grid(True, alpha=0.3)

        ax1.set_title(f"{self.code} 分时图", fontsize=12, fontweight="bold")
        self.canvas.draw()

    def _draw_kline(self):
        """绘制K线图 (日线/周线/月线)"""
        self.canvas.fig.clear()

        if not self.klines:
            self.canvas.draw()
            return

        # 转换为DataFrame (mplfinance格式)
        data = {
            "Date": pd.to_datetime([k.date for k in self.klines]),
            "Open": [k.open for k in self.klines],
            "High": [k.high for k in self.klines],
            "Low": [k.low for k in self.klines],
            "Close": [k.close for k in self.klines],
            "Volume": [k.volume for k in self.klines],
        }
        df = pd.DataFrame(data)
        df.set_index("Date", inplace=True)

        # 计算MA
        ma_lines = []
        for period in MA_PERIODS:
            if len(df) >= period:
                ma = df["Close"].rolling(window=period).mean()
                # 使用 mplfinance 的 addplot 添加MA
                color_idx = MA_PERIODS.index(period) % len(CHART_COLORS["ma_colors"])
                ma_lines.append(mpf.make_addplot(
                    ma, color=CHART_COLORS["ma_colors"][color_idx],
                    width=1.0, label=f"MA{period}"
                ))

        # 止损止盈线标注 (从外部设置)
        stop_loss_line = None
        take_profit_line = None
        if hasattr(self, 'stop_loss_price') and self.stop_loss_price > 0:
            sl_data = pd.Series(self.stop_loss_price, index=df.index)
            stop_loss_line = mpf.make_addplot(
                sl_data, color=CHART_COLORS["alert_stop_loss"],
                linestyle="--", width=1.0, label="止损"
            )
            ma_lines.append(stop_loss_line)

        if hasattr(self, 'take_profit_price') and self.take_profit_price > 0:
            tp_data = pd.Series(self.take_profit_price, index=df.index)
            take_profit_line = mpf.make_addplot(
                tp_data, color=CHART_COLORS["alert_take_profit"],
                linestyle="--", width=1.0, label="止盈"
            )
            ma_lines.append(take_profit_line)

        # 周线底分型标注 (如果有)
        if hasattr(self, 'bottom_fractal_dates') and self.bottom_fractal_dates:
            markers = pd.Series(
                [df.loc[d, "Low"] * 0.98 if d in df.index else np.nan
                 for d in self.bottom_fractal_dates],
            )
            # 简单标注不通过mpf

        period_names = {
            "daily": "日线", "weekly": "周线", "monthly": "月线",
            "60min": "60分钟线",
        }
        title = f"{self.code} {period_names.get(self.period, self.period)}K线图"

        try:
            if ma_lines:
                mpf.plot(
                    df, type="candle", style=mpf_style,
                    volume=True, addplot=ma_lines,
                    title=title,
                    ylabel="价格",
                    ylabel_lower="成交量",
                    figsize=(10, 6),
                    panel_ratios=(3, 1),
                    ax=self.canvas.fig.add_subplot(1, 1, 1) if ma_lines else None,
                    returnfig=True,
                )
            else:
                mpf.plot(
                    df, type="candle", style=mpf_style,
                    volume=True,
                    title=title,
                    ylabel="价格",
                    ylabel_lower="成交量",
                    figsize=(10, 6),
                    panel_ratios=(3, 1),
                )
        except Exception:
            # 降级: 使用默认样式
            pass

        # 直接在figure上绘制
        self._draw_kline_manual(df, title, ma_lines)

        self.canvas.draw()

    def _draw_kline_manual(self, df: pd.DataFrame, title: str, addplots: list):
        """手动绘制K线图 (备用方式，更可控)"""
        self.canvas.fig.clear()

        # 创建上下两个子图
        ax1 = self.canvas.fig.add_subplot(2, 1, 1)
        ax2 = self.canvas.fig.add_subplot(2, 1, 2, sharex=ax1)

        # --- 上栏: K线 + MA ---
        # 蜡烛图绘制
        width = 0.6
        for i, (idx, row) in enumerate(df.iterrows()):
            color = CHART_COLORS["up"] if row["Close"] >= row["Open"] else CHART_COLORS["down"]
            # 影线
            ax1.plot([i, i], [row["Low"], row["High"]], color=color, linewidth=0.8)
            # 实体
            body_bottom = min(row["Open"], row["Close"])
            body_height = abs(row["Close"] - row["Open"])
            if body_height < 0.0001:
                body_height = max(row["High"] - row["Low"], 0.001)
            ax1.bar(i, body_height, width=width, bottom=body_bottom, color=color, alpha=0.9)

        # MA线
        for period in MA_PERIODS:
            if len(df) >= period:
                ma = df["Close"].rolling(window=period).mean()
                color_idx = MA_PERIODS.index(period) % len(CHART_COLORS["ma_colors"])
                ax1.plot(range(len(df)), ma.values,
                        color=CHART_COLORS["ma_colors"][color_idx],
                        linewidth=1.0, label=f"MA{period}", alpha=0.8)

        # 止损止盈线
        if hasattr(self, 'stop_loss_price') and self.stop_loss_price > 0:
            ax1.axhline(y=self.stop_loss_price, color=CHART_COLORS["alert_stop_loss"],
                       linestyle="--", linewidth=1.0, label=f"止损 {self.stop_loss_price:.2f}")
        if hasattr(self, 'take_profit_price') and self.take_profit_price > 0:
            ax1.axhline(y=self.take_profit_price, color=CHART_COLORS["alert_take_profit"],
                       linestyle="--", linewidth=1.0, label=f"止盈 {self.take_profit_price:.2f}")

        # 底分型标注
        if hasattr(self, 'bottom_fractal_indices') and self.bottom_fractal_indices:
            for bf_idx in self.bottom_fractal_indices:
                if 0 <= bf_idx < len(df):
                    low = df.iloc[bf_idx]["Low"]
                    ax1.scatter(bf_idx, low * 0.98, marker="^", color="blue", s=80, zorder=5)

        ax1.set_title(title, fontsize=12, fontweight="bold")
        ax1.set_ylabel("价格")
        ax1.legend(loc="upper left", fontsize=7, ncol=2)
        ax1.grid(True, alpha=0.3)

        # x轴标签
        step = max(1, len(df) // 10)
        tick_idx = list(range(0, len(df), step))
        tick_labels = [df.index[i].strftime("%Y-%m-%d") if hasattr(df.index[i], 'strftime')
                       else str(df.index[i])[:10] for i in tick_idx]
        ax1.set_xticks(tick_idx)
        ax1.set_xticklabels(tick_labels, rotation=30, fontsize=7)

        # --- 下栏: 成交量 ---
        colors_vol = [CHART_COLORS["volume_up"] if row["Close"] >= row["Open"]
                      else CHART_COLORS["volume_down"] for _, row in df.iterrows()]
        ax2.bar(range(len(df)), df["Volume"].values, color=colors_vol, width=width, alpha=0.7)
        ax2.set_ylabel("成交量")
        ax2.set_xticks(tick_idx)
        ax2.set_xticklabels(tick_labels, rotation=30, fontsize=7)
        ax2.grid(True, alpha=0.3)

        self.canvas.fig.tight_layout()

    # 外部接口
    def set_alert_lines(self, stop_loss: float, take_profit: float):
        """设置止损止盈显示线"""
        self.stop_loss_price = stop_loss
        self.take_profit_price = take_profit

    def set_bottom_fractals(self, indices: list[int], dates: list[str]):
        """设置底分型位置"""
        self.bottom_fractal_indices = indices
        self.bottom_fractal_dates = dates


class ChartWidget(QWidget):
    """图表组件 (含周期切换标签)"""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.tabs = QTabWidget()
        self.intraday_tab = ChartTabWidget("intraday")
        self.daily_tab = ChartTabWidget("daily")
        self.weekly_tab = ChartTabWidget("weekly")
        self.monthly_tab = ChartTabWidget("monthly")

        self.tabs.addTab(self.intraday_tab, "分时")
        self.tabs.addTab(self.daily_tab, "日线")
        self.tabs.addTab(self.weekly_tab, "周线")
        self.tabs.addTab(self.monthly_tab, "月线")

        layout.addWidget(self.tabs)

    def load_stock(self, code: str):
        """加载股票全部周期数据"""
        self.intraday_tab.load_data(code)
        self.daily_tab.load_data(code)
        self.weekly_tab.load_data(code)
        self.monthly_tab.load_data(code)

    def set_alert_lines(self, stop_loss: float, take_profit: float):
        """设置所有周期图表的止损止盈线"""
        for tab in [self.intraday_tab, self.daily_tab, self.weekly_tab, self.monthly_tab]:
            tab.set_alert_lines(stop_loss, take_profit)
