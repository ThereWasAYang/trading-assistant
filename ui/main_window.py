"""主窗口 — 布局、菜单、系统托盘、实时数据轮询、止损止盈检查、买点扫描"""

import os
from datetime import datetime

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QSplitter, QVBoxLayout, QHBoxLayout,
    QTreeWidget, QTreeWidgetItem, QTableView, QTabWidget,
    QStatusBar, QLabel, QMenuBar, QAction, QMessageBox,
    QSystemTrayIcon, QMenu, QApplication, QHeaderView,
    QAbstractItemView,
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QIcon, QColor, QFont

from config import (
    WINDOW_TITLE, WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT,
    SIDEBAR_WIDTH, REALTIME_REFRESH_MS, BUYPOINT_SCAN_INTERVAL_MS,
    KLINE_REFRESH_MS, DAILY_STOP_LOSS_HOUR, DAILY_STOP_LOSS_MINUTE,
    STOCK_TABLE_COLUMNS, PRESET_GROUPS, CHART_COLORS,
)
from data.database import (
    init_db, get_all_groups, get_stocks_by_group, get_all_stocks,
    add_stock, remove_stock, move_stock, get_all_trades, get_position_summary,
    is_alert_disabled, disable_alert, enable_alert,
)
from data.market_data import (
    RealtimeWorker, KLineWorker, IntradayWorker, StockSearchWorker,
)
from data.models import RealtimeQuote, Group, Stock
from core.alert_engine import AlertEngine
from core.buy_point_scanner import BuyPointScanWorker
from utils.logger import get_logger

logger = get_logger(__name__)


class MainWindow(QMainWindow):
    """交易系统主窗口"""

    # 信号
    quote_updated = pyqtSignal(dict)  # 实时行情更新 {code: RealtimeQuote}

    def __init__(self):
        super().__init__()
        init_db()
        logger.info("A股交易辅助系统启动中...")

        self.setWindowTitle(WINDOW_TITLE)
        self.setMinimumSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)

        # ---- 引擎 ----
        self.alert_engine = AlertEngine()

        # ---- 内部状态 ----
        self._current_group_id: int = -1
        self._current_stock_code: str = ""
        self._quotes_cache: dict[str, RealtimeQuote] = {}
        self._buy_point_states: dict[str, dict] = {}
        self._tray_flash_timer: QTimer = None
        self._tray_flash_on: bool = False
        self._alert_triggered_codes: set[str] = set()
        self._bp_triggered_codes: set[str] = set()
        self._daily_stop_loss_done: set[str] = set()  # 今日已执行每日止损更新的代码

        # 构建UI
        self._setup_menu()
        self._setup_ui()
        self._setup_tray()
        self._setup_timers()
        self._load_groups()

        # 初始刷新
        self._refresh_current_group_data()

        logger.info("主窗口初始化完成")

    # ================================================================
    # 菜单
    # ================================================================

    def _setup_menu(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("文件(&F)")
        act_add_stock = QAction("添加股票(&A)", self)
        act_add_stock.triggered.connect(self._on_add_stock)
        file_menu.addAction(act_add_stock)
        file_menu.addSeparator()
        act_exit = QAction("退出(&X)", self)
        act_exit.triggered.connect(self.close)
        file_menu.addAction(act_exit)

        group_menu = menubar.addMenu("分组(&G)")
        act_new_group = QAction("新建自定义分组(&N)", self)
        act_new_group.triggered.connect(self._on_new_group)
        group_menu.addAction(act_new_group)
        act_del_group = QAction("删除当前分组(&D)", self)
        act_del_group.triggered.connect(self._on_delete_group)
        group_menu.addAction(act_del_group)

        view_menu = menubar.addMenu("视图(&V)")
        act_refresh = QAction("刷新数据(&R)\tF5", self)
        act_refresh.triggered.connect(self._refresh_current_group_data)
        view_menu.addAction(act_refresh)

        help_menu = menubar.addMenu("帮助(&H)")
        act_about = QAction("关于(&A)", self)
        act_about.triggered.connect(self._on_about)
        help_menu.addAction(act_about)

    # ================================================================
    # 主界面布局
    # ================================================================

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)

        # ---- 左侧分组面板 ----
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        left_label = QLabel("分组列表")
        left_label.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        left_layout.addWidget(left_label)

        self.group_tree = QTreeWidget()
        self.group_tree.setHeaderHidden(True)
        self.group_tree.setFixedWidth(SIDEBAR_WIDTH)
        self.group_tree.setRootIsDecorated(True)
        self.group_tree.currentItemChanged.connect(self._on_group_selected)
        left_layout.addWidget(self.group_tree)

        # ---- 右侧面板 ----
        right_splitter = QSplitter(Qt.Vertical)

        from ui.stock_table import StockTableWidget
        self.stock_table = StockTableWidget()
        self.stock_table.stock_double_clicked.connect(self._on_stock_double_clicked)
        self.stock_table.stock_right_clicked.connect(self._on_stock_right_clicked)
        right_splitter.addWidget(self.stock_table)

        from ui.chart_widget import ChartWidget
        self.chart_widget = ChartWidget()
        right_splitter.addWidget(self.chart_widget)

        right_splitter.setStretchFactor(0, 3)
        right_splitter.setStretchFactor(1, 5)

        # ---- 分割器 ----
        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.addWidget(left_widget)
        main_splitter.addWidget(right_splitter)
        main_splitter.setStretchFactor(0, 0)
        main_splitter.setStretchFactor(1, 1)

        main_layout.addWidget(main_splitter)

        # ---- 状态栏 ----
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self._status_refresh_label = QLabel("上次刷新: --")
        self._status_profit_label = QLabel("持仓盈亏: --")
        self._status_buypoint_label = QLabel("买点: 无")
        self.status_bar.addWidget(self._status_refresh_label)
        self.status_bar.addPermanentWidget(self._status_profit_label)
        self.status_bar.addPermanentWidget(self._status_buypoint_label)

    # ================================================================
    # 系统托盘
    # ================================================================

    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("系统托盘不可用")
            return

        self.tray_icon = QSystemTrayIcon(self)
        icon_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "resources", "icons", "app.png"
        )
        if os.path.exists(icon_path):
            self.tray_icon.setIcon(QIcon(icon_path))
        else:
            self.tray_icon.setIcon(self.style().standardIcon(
                self.style().SP_ComputerIcon
            ))

        tray_menu = QMenu()
        act_show = QAction("显示主窗口", self)
        act_show.triggered.connect(self.showNormal)
        tray_menu.addAction(act_show)
        act_hide = QAction("最小化到托盘", self)
        act_hide.triggered.connect(self.hide)
        tray_menu.addAction(act_hide)
        tray_menu.addSeparator()
        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self.close)
        tray_menu.addAction(act_quit)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()

        self.tray_icon.messageClicked.connect(self.showNormal)
        self.tray_icon.activated.connect(self._on_tray_activated)

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.showNormal()
            self.activateWindow()
            # 点击托盘时停止闪烁（用户已注意到）
            self.flash_tray(False)
            self._clear_all_highlights()

    def flash_tray(self, enable: bool = True):
        """启动/停止托盘图标闪烁"""
        if not hasattr(self, 'tray_icon'):
            return

        if enable and self._alert_triggered_codes:
            if self._tray_flash_timer is None:
                self._tray_flash_timer = QTimer(self)
                self._tray_flash_timer.timeout.connect(self._toggle_tray_icon)
                self._tray_flash_timer.start(500)
        else:
            if self._tray_flash_timer:
                self._tray_flash_timer.stop()
                self._tray_flash_timer = None
            self.tray_icon.setIcon(self.style().standardIcon(
                self.style().SP_ComputerIcon
            ))
            self._tray_flash_on = False

    def _toggle_tray_icon(self):
        """切换托盘图标 (闪烁效果)"""
        if self._tray_flash_on:
            self.tray_icon.setIcon(self.style().standardIcon(
                self.style().SP_ComputerIcon
            ))
        else:
            self.tray_icon.setIcon(self.style().standardIcon(
                self.style().SP_MessageBoxWarning
            ))
        self._tray_flash_on = not self._tray_flash_on

    # ================================================================
    # 定时器
    # ================================================================

    def _setup_timers(self):
        # 实时行情刷新 (10秒)
        self._realtime_timer = QTimer(self)
        self._realtime_timer.timeout.connect(self._refresh_current_group_data)
        self._realtime_timer.start(REALTIME_REFRESH_MS)

        # 买点扫描 (5分钟，异步不阻塞UI)
        self._buypoint_timer = QTimer(self)
        self._buypoint_timer.timeout.connect(self._scan_buy_points)
        self._buypoint_timer.start(BUYPOINT_SCAN_INTERVAL_MS)

        # K线数据刷新 (30秒)
        self._kline_timer = QTimer(self)
        self._kline_timer.timeout.connect(self._refresh_kline_if_active)
        self._kline_timer.start(KLINE_REFRESH_MS)

        # 每日止损更新检查 (每分钟检查一次是否到15:05)
        self._daily_sl_timer = QTimer(self)
        self._daily_sl_timer.timeout.connect(self._check_daily_stop_loss)
        self._daily_sl_timer.start(60 * 1000)

    # ================================================================
    # 分组管理
    # ================================================================

    def _load_groups(self):
        """加载分组到左侧树"""
        self.group_tree.blockSignals(True)
        self.group_tree.clear()

        groups = get_all_groups()
        type_order = {"holding": 0, "cleared": 1, "tracking": 2, "custom": 3}
        type_labels = {"holding": "📁 持仓中", "cleared": "📁 已清仓",
                       "tracking": "📁 跟踪中", "custom": "📁 "}

        groups.sort(key=lambda g: (type_order.get(g.type, 99), g.sort_order))

        current_type = None
        type_parent = None
        for g in groups:
            if g.type != current_type:
                type_parent = QTreeWidgetItem(self.group_tree)
                if g.type == "custom":
                    type_parent.setText(0, "📁 自定义")
                else:
                    type_parent.setText(0, type_labels.get(g.type, g.name))
                type_parent.setData(0, Qt.UserRole, -1)
                type_parent.setExpanded(True)
                current_type = g.type

            item = QTreeWidgetItem(type_parent)
            item.setText(0, g.name)
            item.setData(0, Qt.UserRole, g.id)
            item.setData(0, Qt.UserRole + 1, g.type)

        self.group_tree.blockSignals(False)

        if self.group_tree.topLevelItemCount() > 0:
            first_parent = self.group_tree.topLevelItem(0)
            if first_parent.childCount() > 0:
                self.group_tree.setCurrentItem(first_parent.child(0))

    def _on_group_selected(self, current, previous):
        if current is None:
            return
        group_id = current.data(0, Qt.UserRole)
        if group_id is None or group_id < 0:
            return
        self._current_group_id = group_id
        self._refresh_current_group_data()

    # ================================================================
    # 数据刷新
    # ================================================================

    def _refresh_current_group_data(self):
        """刷新当前选中分组的股票数据"""
        codes = self._get_all_tracked_codes()
        if not codes:
            return

        self._realtime_worker = RealtimeWorker(codes)
        self._realtime_worker.data_ready.connect(self._on_quote_data_ready)
        self._realtime_worker.error_occurred.connect(
            lambda e: logger.error(f"行情数据错误: {e}"))
        self._realtime_worker.start()

    def _refresh_kline_if_active(self):
        """如果当前有正在查看的股票，自动刷新K线"""
        if self._current_stock_code:
            logger.debug(f"自动刷新K线: {self._current_stock_code}")
            self.chart_widget.load_stock(self._current_stock_code)

    def _get_all_tracked_codes(self) -> list[str]:
        """获取所有需要监控的股票代码"""
        stocks = get_all_stocks()
        return list(set(s.code for s in stocks))

    def _get_current_group_codes(self) -> list[str]:
        """获取当前选中分组的股票代码"""
        if self._current_group_id < 0:
            return []
        stocks = get_stocks_by_group(self._current_group_id)
        return [s.code for s in stocks]

    def _on_quote_data_ready(self, quotes: dict[str, RealtimeQuote]):
        """实时行情数据到达"""
        self._quotes_cache = quotes

        # 更新当前分组表格
        codes = self._get_current_group_codes()
        current_quotes = {c: quotes[c] for c in codes if c in quotes}
        self.stock_table.update_quotes(current_quotes, self._alert_triggered_codes,
                                       self._bp_triggered_codes)

        # 更新状态栏
        now = datetime.now().strftime("%H:%M:%S")
        self._status_refresh_label.setText(f"上次刷新: {now}")

        # 检查止损止盈 (使用AlertEngine)
        self._check_alerts(quotes)

        # 更新状态栏盈亏
        self._update_profit_status(quotes)

    def _update_profit_status(self, quotes: dict[str, RealtimeQuote]):
        """更新持仓盈亏状态栏"""
        holding_group = None
        for g in get_all_groups():
            if g.type == "holding":
                holding_group = g
                break
        if holding_group is None:
            return

        stocks = get_stocks_by_group(holding_group.id)
        total_profit = 0.0
        total_cost = 0.0
        for s in stocks:
            summary = get_position_summary(s.code)
            if summary["hold_qty"] > 0 and s.code in quotes:
                q = quotes[s.code]
                profit = (q.price - summary["avg_cost"]) * summary["hold_qty"]
                total_profit += profit
                total_cost += summary["avg_cost"] * summary["hold_qty"]

        if total_cost > 0:
            pct = total_profit / total_cost * 100
            color = "red" if total_profit >= 0 else "green"
            self._status_profit_label.setText(
                f"持仓盈亏: <span style='color:{color}'>{total_profit:+.2f} ({pct:+.2f}%)</span>"
            )
            self._status_profit_label.setTextFormat(Qt.RichText)

    # ================================================================
    # 每日止损更新
    # ================================================================

    def _check_daily_stop_loss(self):
        """检查是否到达每日止损更新时间 (15:05)"""
        now = datetime.now()
        if now.hour != DAILY_STOP_LOSS_HOUR or now.minute != DAILY_STOP_LOSS_MINUTE:
            return
        if now.weekday() >= 5:
            return

        today_str = now.strftime("%Y-%m-%d")
        holding_codes = self._get_holding_codes()
        for code in holding_codes:
            if code not in self._daily_stop_loss_done:
                new_stop, conflict = self.alert_engine.update_daily_stop_loss(code)
                if conflict:
                    # 手动止损与自动计算冲突 → 弹窗确认
                    self._show_alert_conflict(code, conflict)
                else:
                    self._daily_stop_loss_done.add(code)
                    logger.info(f"[{today_str}] {code} 收盘止损更新: {new_stop:.2f}")

        # 如果日期变了，清空标记
        self._daily_stop_loss_done = {
            c for c in self._daily_stop_loss_done
            if c in holding_codes
        }

    def _get_holding_codes(self) -> list[str]:
        """获取持仓中的代码列表"""
        for g in get_all_groups():
            if g.type == "holding":
                stocks = get_stocks_by_group(g.id)
                return [s.code for s in stocks]
        return []

    # ================================================================
    # 止损止盈检查 (使用AlertEngine)
    # ================================================================

    def _check_alerts(self, quotes: dict[str, RealtimeQuote]):
        """检查止损止盈触发 (AlertEngine) + 自动更新冲突检测"""
        holding_codes = self._get_holding_codes()
        triggered = []

        for code in holding_codes:
            if code not in quotes:
                continue
            if is_alert_disabled(code):
                self._alert_triggered_codes.discard(code)
                continue

            quote = quotes[code]

            # 计算止损线 (用今日最低价更新)，检查手动冲突
            _, sl_conflict = self.alert_engine.calc_stop_loss(
                code, quote.low if quote.low > 0 else quote.price
            )
            if sl_conflict:
                self._show_alert_conflict(code, sl_conflict)

            # 计算止盈线 (检查30min顶分型)，检查手动冲突
            _, tp_conflict = self.alert_engine.calc_take_profit(code, quote.price)
            if tp_conflict:
                self._show_alert_conflict(code, tp_conflict)

            # 检查触发
            result = self.alert_engine.check_alerts(code, quote)
            if result["triggered"]:
                triggered.append((
                    code, result["type"], result["trigger_price"], quote.price,
                    result.get("message", "")
                ))
                self._alert_triggered_codes.add(code)
            else:
                self._alert_triggered_codes.discard(code)

        if triggered:
            self._on_alerts_triggered(triggered)
        else:
            # 更新高亮（清除不再触发的）
            alert_codes = list(self._alert_triggered_codes)
            if alert_codes:
                self.stock_table.highlight_rows(alert_codes, "alert")
            else:
                self.stock_table.clear_highlights()
                self.flash_tray(False)

    def _on_alerts_triggered(self, triggered: list[tuple]):
        """提醒触发处理"""
        self.flash_tray(True)

        alert_codes = list(self._alert_triggered_codes)
        self.stock_table.highlight_rows(alert_codes, "alert")

        msgs = [f"{code} {reason}: 触发价={trigger:.2f} 现价={price:.2f}"
                for code, reason, trigger, price, _ in triggered]
        self.tray_icon.showMessage(
            "⚠ 交易提醒",
            "\n".join(msgs[:3]) + ("..." if len(msgs) > 3 else ""),
            QSystemTrayIcon.Warning,
            5000,
        )

    def _show_alert_conflict(self, code: str, conflict: dict):
        """
        手动设置与自动计算冲突 → 弹窗确认
        conflict: {field: 'sl'|'tp', auto_value: float, manual_value: float}
        """
        from PyQt5.QtWidgets import QMessageBox

        field_name = "止损" if conflict["field"] == "sl" else "止盈"
        auto_val = conflict["auto_value"]
        manual_val = conflict["manual_value"]

        reply = QMessageBox.question(
            self,
            f"止盈止损冲突 - {code}",
            f"系统自动计算的{field_name}价 (¥{auto_val:.2f})\n"
            f"与您手动设置的{field_name}价 (¥{manual_val:.2f}) 不一致。\n\n"
            f"选择「覆盖」: 放弃手动设置，使用系统自动值 ¥{auto_val:.2f}\n"
            f"选择「保留」: 继续使用手动设置 ¥{manual_val:.2f}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )

        if reply == QMessageBox.Yes:
            # 用户选择覆盖 → 使用系统自动值
            if conflict["field"] == "sl":
                self.alert_engine.clear_manual(code, "sl")
                self.alert_engine.get_state(code).stop_loss_price = auto_val
            else:
                self.alert_engine.clear_manual(code, "tp")
                self.alert_engine.get_state(code).take_profit_price = auto_val
            logger.info(f"{code} 用户选择覆盖手动{field_name}: {manual_val:.2f} → {auto_val:.2f}")
        else:
            logger.info(f"{code} 用户选择保留手动{field_name}: {manual_val:.2f}")

    # ================================================================
    # 买点扫描 (异步)
    # ================================================================

    def _scan_buy_points(self):
        """异步扫描所有跟踪股票的买点 (使用BuyPointScanWorker)"""
        codes = self._get_all_tracked_codes()
        if not codes:
            return

        logger.debug(f"开始异步买点扫描: {len(codes)} 只股票")
        for code in codes:
            worker = BuyPointScanWorker(code)
            worker.scan_done.connect(self._on_buy_point_result)
            worker.start()

    def _on_buy_point_result(self, code: str, result: dict):
        """买点扫描结果回调（主线程）"""
        if result.get("triggered"):
            self._buy_point_states[code] = result
            self._bp_triggered_codes.add(code)
            logger.info(f"🟡 {code} 买点触发! {result.get('signal_details', '')}")
        else:
            self._bp_triggered_codes.discard(code)
            if code in self._buy_point_states:
                del self._buy_point_states[code]

        buy_codes = list(self._bp_triggered_codes)
        self._status_buypoint_label.setText(
            f"买点: {len(buy_codes)}只" if buy_codes else "买点: 无"
        )

        if buy_codes:
            self.stock_table.highlight_rows(buy_codes, "buy_point")
            self.flash_tray(True)
        else:
            self.stock_table.clear_highlights()
            if not self._alert_triggered_codes:
                self.flash_tray(False)

    # ================================================================
    # 股票操作
    # ================================================================

    def _on_stock_double_clicked(self, code: str):
        """双击股票行 → 切换图表 + 停止闪烁 + (如有买点)弹交易纪律"""
        self._current_stock_code = code
        self.chart_widget.load_stock(code)

        # 用户点击了触发提醒的股票 → 停止托盘闪烁和表格高亮
        if code in self._alert_triggered_codes:
            self._alert_triggered_codes.discard(code)
            if not self._alert_triggered_codes:
                self.flash_tray(False)
                self.stock_table.clear_highlights()
            else:
                self.stock_table.highlight_rows(list(self._alert_triggered_codes), "alert")

        # 如果有买点，弹出交易纪律弹窗
        bp = self._buy_point_states.get(code, {})
        if bp.get("triggered"):
            from ui.discipline_dialog import DisciplineDialog
            dlg = DisciplineDialog(code, self)
            dlg.set_signal_info(bp.get("signal_details", ""))
            dlg.exec_()
            # 弹窗关闭后也停止买点闪烁
            self._bp_triggered_codes.discard(code)
            if not self._bp_triggered_codes and not self._alert_triggered_codes:
                self.flash_tray(False)
                self.stock_table.clear_highlights()

    def _on_stock_right_clicked(self, code: str, action: str):
        """股票右键菜单操作"""
        if action == "add_trade":
            from ui.trade_dialog import TradeDialog
            dlg = TradeDialog(code, self)
            dlg.exec_()
        elif action == "manual_alert":
            self._on_manual_alert_settings(code)
        elif action == "disable_alert":
            disable_alert(code)
            self._alert_triggered_codes.discard(code)
            self._bp_triggered_codes.discard(code)
            if not self._alert_triggered_codes:
                self.flash_tray(False)
                self.stock_table.clear_highlights()
            logger.info(f"{code} 提醒已手动关闭")
        elif action == "enable_alert":
            enable_alert(code)
            logger.info(f"{code} 提醒已手动开启")
        elif action == "remove_stock":
            stocks = get_stocks_by_group(self._current_group_id)
            for s in stocks:
                if s.code == code:
                    remove_stock(s.id)
                    break
            self._refresh_current_group_data()
        elif action == "move_to_cleared":
            self._move_to_cleared(code)

    def _move_to_cleared(self, code: str):
        """将股票移到已清仓分组"""
        cleared_id = None
        for g in get_all_groups():
            if g.type == "cleared":
                cleared_id = g.id
                break
        if cleared_id:
            stocks = get_stocks_by_group(self._current_group_id)
            for s in stocks:
                if s.code == code:
                    move_stock(s.id, cleared_id)
                    break
            self._refresh_current_group_data()
            logger.info(f"{code} 已移至已清仓分组")

    def _on_manual_alert_settings(self, code: str):
        """打开手动止盈止损设置对话框"""
        from ui.alert_settings_dialog import AlertSettingsDialog
        quote = self._quotes_cache.get(code)
        dlg = AlertSettingsDialog(code, quote, self)
        if dlg.exec_() == AlertSettingsDialog.Accepted:
            result = dlg.get_result()
            # 应用手动设置到 AlertEngine
            if result.get("sl_active"):
                self.alert_engine.set_manual_sl(code, result["sl_price"])
            else:
                self.alert_engine.clear_manual(code, "sl")

            if result.get("tp_active"):
                self.alert_engine.set_manual_tp(code, result["tp_price"])
            else:
                self.alert_engine.clear_manual(code, "tp")

            self._refresh_current_group_data()

    def _clear_all_highlights(self):
        """清除所有高亮"""
        self.stock_table.clear_highlights()

    def _on_add_stock(self):
        """添加股票"""
        from PyQt5.QtWidgets import QInputDialog

        keyword, ok = QInputDialog.getText(self, "添加股票", "输入股票代码或名称:")
        if not ok or not keyword.strip():
            return

        keyword = keyword.strip()
        self._search_worker = StockSearchWorker(keyword)
        self._search_worker.data_ready.connect(
            lambda results: self._on_search_result(results, keyword))
        self._search_worker.start()

    def _on_search_result(self, results: list[dict], keyword: str):
        """搜索结果回调"""
        from PyQt5.QtWidgets import QDialog, QListWidget, QVBoxLayout, QDialogButtonBox, QListWidgetItem

        if not results:
            QMessageBox.information(self, "搜索", f"未找到 '{keyword}'")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("选择股票")
        dlg.setMinimumSize(350, 400)
        layout = QVBoxLayout(dlg)

        list_widget = QListWidget()
        for r in results:
            item = QListWidgetItem(f"{r['code']}  {r['name']}  ¥{r['price']:.2f}")
            item.setData(Qt.UserRole, r)
            list_widget.addItem(item)
        layout.addWidget(list_widget)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        if dlg.exec_() != QDialog.Accepted:
            return

        selected = list_widget.currentItem()
        if selected is None:
            return

        r = selected.data(Qt.UserRole)
        if self._current_group_id < 0:
            for g in get_all_groups():
                if g.type == "tracking":
                    self._current_group_id = g.id
                    break

        stock = add_stock(r["code"], r["name"], self._current_group_id)
        if stock:
            self._refresh_current_group_data()
            logger.info(f"添加股票: {r['code']} {r['name']}")
        else:
            QMessageBox.information(self, "提示", f"股票 {r['code']} 已存在于此分组")

    def _on_new_group(self):
        """新建自定义分组"""
        from PyQt5.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "新建分组", "分组名称:")
        if ok and name.strip():
            from data.database import add_group
            add_group(name.strip(), "custom")
            self._load_groups()
            logger.info(f"新建自定义分组: {name.strip()}")

    def _on_delete_group(self):
        """删除当前自定义分组"""
        if self._current_group_id < 0:
            return
        for g in get_all_groups():
            if g.id == self._current_group_id:
                if g.type != "custom":
                    QMessageBox.warning(self, "提示", "不能删除系统分组")
                    return
                reply = QMessageBox.question(
                    self, "确认", f"确定删除分组 '{g.name}' 及其所有股票?",
                    QMessageBox.Yes | QMessageBox.No,
                )
                if reply == QMessageBox.Yes:
                    from data.database import delete_group
                    delete_group(g.id)
                    self._current_group_id = -1
                    self._load_groups()
                    self.stock_table.clear()
                    logger.info(f"删除分组: {g.name}")
                return

    def _on_about(self):
        QMessageBox.about(
            self, "关于",
            "A股交易辅助系统 v1.0\n\n"
            "功能:\n"
            "• 实时股票数据与K线图表\n"
            "• 持仓/已清仓/跟踪分组管理\n"
            "• 止损止盈线自动计算与提醒\n"
            "• 买点扫描 (底分型/MACD金叉/回踩中枢)\n"
            "• 交易纪律提醒\n\n"
            "数据来源: AKShare"
        )

    # ================================================================
    # 生命周期
    # ================================================================

    def closeEvent(self, event):
        """关闭窗口事件"""
        logger.info("系统退出")
        self.flash_tray(False)
        if hasattr(self, 'tray_icon'):
            self.tray_icon.hide()
        event.accept()
