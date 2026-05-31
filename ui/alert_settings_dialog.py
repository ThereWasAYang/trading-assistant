"""手动止盈止损设置对话框"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QCheckBox, QPushButton, QLabel, QGroupBox,
    QDialogButtonBox, QMessageBox,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

from data.database import get_manual_alert, set_manual_alert, clear_manual_alert
from data.market_data import RealtimeQuote
from utils.logger import get_logger

logger = get_logger(__name__)


class AlertSettingsDialog(QDialog):
    """手动设置止盈止损价格"""

    def __init__(self, code: str, quote: RealtimeQuote = None, parent=None):
        super().__init__(parent)
        self.code = code
        self.quote = quote or RealtimeQuote()

        self._result: dict = {}  # 供外部读取设置结果

        self.setWindowTitle(f"止盈止损设置 - {code}")
        self.setMinimumWidth(420)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        self._setup_ui()
        self._load_current()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # ---- 当前价格提示 ----
        info_text = f"当前价格: ¥{self.quote.price:.2f}" if self.quote.price > 0 else ""
        if info_text:
            lbl_info = QLabel(info_text)
            lbl_info.setFont(QFont("Microsoft YaHei", 10))
            lbl_info.setAlignment(Qt.AlignCenter)
            lbl_info.setStyleSheet("color: #555;")
            layout.addWidget(lbl_info)

        # ---- 止损设置 ----
        sl_group = QGroupBox("止损线 (只上移不下移)")
        sl_layout = QFormLayout(sl_group)

        self.sl_check = QCheckBox("启用手动止损")
        sl_layout.addRow(self.sl_check)

        self.sl_edit = QLineEdit()
        self.sl_edit.setPlaceholderText("输入止损价格，如 10.50")
        self.sl_edit.setMinimumWidth(200)
        sl_layout.addRow("止损价格:", self.sl_edit)

        layout.addWidget(sl_group)

        # ---- 止盈设置 ----
        tp_group = QGroupBox("止盈线")
        tp_layout = QFormLayout(tp_group)

        self.tp_check = QCheckBox("启用手动止盈")
        tp_layout.addRow(self.tp_check)

        self.tp_edit = QLineEdit()
        self.tp_edit.setPlaceholderText("输入止盈价格，如 12.00")
        tp_layout.addRow("止盈价格:", self.tp_edit)

        layout.addWidget(tp_group)

        # ---- 提示 ----
        hint = QLabel("提示: 取消勾选即可恢复系统自动计算。\n手动设置后，系统更新时将弹窗确认是否覆盖。")
        hint.setStyleSheet("color: #999; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # ---- 按钮 ----
        btn_layout = QHBoxLayout()

        self.btn_clear_sl = QPushButton("清除手动止损")
        self.btn_clear_sl.clicked.connect(self._on_clear_sl)
        btn_layout.addWidget(self.btn_clear_sl)

        self.btn_clear_tp = QPushButton("清除手动止盈")
        self.btn_clear_tp.clicked.connect(self._on_clear_tp)
        btn_layout.addWidget(self.btn_clear_tp)

        btn_layout.addStretch()

        self.btn_clear_all = QPushButton("全部恢复自动")
        self.btn_clear_all.clicked.connect(self._on_clear_all)
        btn_layout.addWidget(self.btn_clear_all)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        btn_layout.addWidget(buttons)

        layout.addLayout(btn_layout)

    def _load_current(self):
        """从数据库加载当前手动设置"""
        manual = get_manual_alert(self.code)
        if manual["sl_active"] and manual["sl_price"] > 0:
            self.sl_check.setChecked(True)
            self.sl_edit.setText(str(manual["sl_price"]))
        if manual["tp_active"] and manual["tp_price"] > 0:
            self.tp_check.setChecked(True)
            self.tp_edit.setText(str(manual["tp_price"]))

    def _on_ok(self):
        """保存设置"""
        sl_active = self.sl_check.isChecked()
        tp_active = self.tp_check.isChecked()

        sl_price = 0.0
        tp_price = 0.0

        if sl_active:
            try:
                sl_price = float(self.sl_edit.text().strip())
                if sl_price <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                QMessageBox.warning(self, "输入错误", "请为止损价输入一个有效的正数。")
                return

        if tp_active:
            try:
                tp_price = float(self.tp_edit.text().strip())
                if tp_price <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                QMessageBox.warning(self, "输入错误", "请为止盈价输入一个有效的正数。")
                return

        set_manual_alert(self.code, sl_active, sl_price, tp_active, tp_price)
        logger.info(
            f"{self.code} 手动止盈止损: "
            f"止损={'开启 ¥'+str(sl_price) if sl_active else '自动'}, "
            f"止盈={'开启 ¥'+str(tp_price) if tp_active else '自动'}"
        )

        self._result = {
            "sl_active": sl_active,
            "sl_price": sl_price,
            "tp_active": tp_active,
            "tp_price": tp_price,
        }
        self.accept()

    def _on_clear_sl(self):
        """清除手动止损"""
        self.sl_check.setChecked(False)
        self.sl_edit.clear()

    def _on_clear_tp(self):
        """清除手动止盈"""
        self.tp_check.setChecked(False)
        self.tp_edit.clear()

    def _on_clear_all(self):
        """全部恢复自动"""
        self.sl_check.setChecked(False)
        self.sl_edit.clear()
        self.tp_check.setChecked(False)
        self.tp_edit.clear()

    def get_result(self) -> dict:
        """获取设置结果"""
        return self._result
