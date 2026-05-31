"""交易纪律弹窗 — 买点出现时双击股票弹出"""

import os

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTextEdit,
    QPushButton, QLabel, QCheckBox,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

from data.database import get_discipline_rule, save_discipline_rule, get_setting, set_setting


DEFAULT_DISCIPLINE = """交易纪律:

1. 严格执行止损: 价格触及止损线立即卖出，不得犹豫

2. 不追高: 不在涨幅超过5%时追入

3. 控制仓位: 单只股票仓位不超过总资金的30%

4. 分批建仓: 首次买入不超过计划仓位的50%

5. 顺势而为: 不在下跌趋势中逆势加仓

6. 止盈不贪: 达到止盈目标后逐步减仓

7. 等待买点: 只在买点信号出现后介入

8. 复盘总结: 每笔交易记录盈亏原因"""


class DisciplineDialog(QDialog):
    """交易纪律弹窗"""

    def __init__(self, stock_code: str = "", parent=None):
        super().__init__(parent)
        self.stock_code = stock_code
        self.setWindowTitle(f"交易纪律提醒 - {stock_code}" if stock_code else "交易纪律")
        self.setMinimumSize(500, 450)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        self._setup_ui()
        self._load_rules()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # 标题
        title = QLabel("⚠ 买点信号出现，请回顾交易纪律!")
        title.setFont(QFont("Microsoft YaHei", 13, QFont.Bold))
        title.setStyleSheet("color: #DC143C;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        # 信号详情
        self.signal_label = QLabel("")
        self.signal_label.setAlignment(Qt.AlignCenter)
        self.signal_label.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self.signal_label)

        layout.addSpacing(10)

        # 可编辑的纪律文本
        self.text_edit = QTextEdit()
        self.text_edit.setFont(QFont("Microsoft YaHei", 11))
        self.text_edit.setMinimumHeight(250)
        layout.addWidget(self.text_edit)

        # 底部
        bottom_layout = QHBoxLayout()

        self.check_read = QCheckBox("我已认真阅读并承诺遵守交易纪律")
        bottom_layout.addWidget(self.check_read)

        self.btn_confirm = QPushButton("确认")
        self.btn_confirm.setEnabled(False)
        self.btn_confirm.clicked.connect(self._on_confirm)
        self.btn_confirm.setMinimumWidth(100)
        bottom_layout.addWidget(self.btn_confirm)

        self.btn_save = QPushButton("保存修改")
        self.btn_save.clicked.connect(self._on_save)
        bottom_layout.addWidget(self.btn_save)

        layout.addLayout(bottom_layout)

        # 连接checkbox到确认按钮
        self.check_read.toggled.connect(lambda checked: self.btn_confirm.setEnabled(checked))

    def _load_rules(self):
        """加载交易纪律"""
        # 先从数据库查找该股票的定制规则
        rule = get_discipline_rule(self.stock_code)

        if rule is None:
            # 尝试从文件加载
            rule_text = self._load_default_file()
            self.text_edit.setPlainText(rule_text)
        else:
            self.text_edit.setPlainText(rule.rule_text)

    def _load_default_file(self) -> str:
        """从文件加载默认交易纪律"""
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "resources", "discipline.txt")

        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()

        return DEFAULT_DISCIPLINE

    def _on_save(self):
        """保存交易纪律到数据库"""
        rule_text = self.text_edit.toPlainText().strip()
        if rule_text:
            save_discipline_rule(self.stock_code, rule_text)

            # 也保存默认到文件
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            path = os.path.join(base, "resources", "discipline.txt")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(rule_text)

            self.btn_save.setText("已保存 ✓")

    def _on_confirm(self):
        """确认已阅读"""
        # 记录确认时间
        from datetime import date
        set_setting(
            f"discipline_confirm_{self.stock_code}",
            date.today().isoformat(),
        )
        self.accept()

    def set_signal_info(self, signal_details: str):
        """设置买点信号详情显示"""
        self.signal_label.setText(f"信号: {signal_details}")
