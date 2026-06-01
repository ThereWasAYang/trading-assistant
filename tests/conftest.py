"""测试共用 fixtures"""

import os
import sys
import pytest

# 确保项目根在 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def temp_db():
    """使用临时数据库，测试后自动清理"""
    from config import DB_PATH
    from data.database import init_db

    db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        DB_PATH,
    )

    # 删除旧数据库
    if os.path.exists(db_path):
        os.remove(db_path)

    init_db()
    yield db_path

    # 清理
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest.fixture
def db_conn(temp_db):
    """提供已初始化的数据库连接"""
    from data.database import _connect
    conn = _connect()
    yield conn
    conn.close()
