"""工具函数"""

from datetime import datetime, time


def is_trading_time() -> bool:
    """判断当前是否为A股交易时间 (周一至周五 9:30-11:30, 13:00-15:00)"""
    now = datetime.now()
    if now.weekday() >= 5:  # 周六/周日
        return False
    t = now.time()
    morning_start = time(9, 30)
    morning_end = time(11, 30)
    afternoon_start = time(13, 0)
    afternoon_end = time(15, 0)
    return (morning_start <= t <= morning_end) or (afternoon_start <= t <= afternoon_end)
