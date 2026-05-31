"""统一日志模块 — 控制台+文件双输出，支持滚动日志"""

import logging
import os
from logging.handlers import RotatingFileHandler
from datetime import datetime

# 日志目录
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# 创建根日志器
_logger = logging.getLogger("trading")
_logger.setLevel(logging.DEBUG)
_logger.propagate = False  # 不传递给父日志器

# ---- 格式 ----
_formatter = logging.Formatter(
    "[%(asctime)s] %(levelname)-7s %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ---- 控制台输出 (INFO以上) ----
_console = logging.StreamHandler()
_console.setLevel(logging.INFO)
_console.setFormatter(_formatter)
_logger.addHandler(_console)

# ---- 文件输出 (DEBUG，滚动) ----
_file_handler = RotatingFileHandler(
    os.path.join(LOG_DIR, f"trading_{datetime.now().strftime('%Y%m%d')}.log"),
    maxBytes=5 * 1024 * 1024,  # 5MB
    backupCount=10,
    encoding="utf-8",
)
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(_formatter)
_logger.addHandler(_file_handler)


def get_logger(name: str = "trading") -> logging.Logger:
    """获取模块级日志器（自动继承根日志器配置）"""
    return _logger.getChild(name) if name != "trading" else _logger
