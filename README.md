# A股交易辅助系统

基于 PyQt5 + AKShare 的 A 股实时监控与交易辅助工具，提供 K 线图表、分组管理、动态止损止盈、缠论买点扫描等功能。

## 项目结构

```
trading-assistant/
├── main.py                     # 程序入口
├── config.py                   # 全局配置常量
├── requirements.txt            # Python 依赖
├── resources/
│   └── discipline.txt          # 交易纪律文本（可自定义）
│
├── core/                       # 核心计算引擎
│   ├── technical.py            # 缠论分型检测、MACD/均线计算、中枢/缩量判断
│   ├── alert_engine.py         # 止损止盈引擎（支持手动覆写 + 自动更新冲突检测）
│   └── buy_point_scanner.py    # 买点扫描器（周线底分型 + 日MACD金叉 + 缩量回踩中枢）
│
├── data/                       # 数据层
│   ├── models.py               # 数据模型（Stock, Trade, AlertState, KLineData 等）
│   ├── database.py             # SQLite CRUD（分组/股票/交易/设置/手动止盈止损）
│   └── market_data.py          # AKShare 数据接口封装（异步 QThread + 智能缓存）
│
├── ui/                         # 用户界面
│   ├── main_window.py          # 主窗口（布局/菜单/托盘/定时刷新/冲突弹窗）
│   ├── stock_table.py          # 股票列表表格（高亮/闪烁/排序/右键菜单）
│   ├── chart_widget.py         # K线图/分时图（matplotlib + mplfinance）
│   ├── trade_dialog.py         # 交易记录管理对话框
│   ├── discipline_dialog.py    # 交易纪律弹窗
│   └── alert_settings_dialog.py # 手动止盈止损设置对话框
│
└── utils/                      # 工具模块
    ├── logger.py               # 滚动日志系统（控制台 + 文件）
    └── cache.py                # TTL 智能缓存（防 AKShare 请求封 IP）
```

## 核心功能

### K 线图表
- 分时图、日线、周线、月线 K 线图，Tab 切换周期
- 叠加 MA 均线（5/10/20/60）、止损止盈线
- 底分型标注、成交量柱状图

### 分组管理
- 系统预设：持仓中、已清仓、跟踪中
- 支持自定义分组，拖拽或右键移动股票
- 双击股票行切换图表视图

### 交易记录
- 记录买入/卖出价格、数量、日期、手续费
- 自动计算持仓成本、浮动盈亏
- 清仓后自动移入已清仓分组

### 动态止损止盈
- **止损线**：买入日最低价起步，每日取最高最低价，只上移不下移
- **止盈线**：初始为涨停价；检测到 30 分钟顶分型后锁定为顶分型最高价
- 触发时红色高亮 + 托盘图标闪烁
- 支持手动设置，自动更新冲突时弹窗确认

### 买点扫描
- 三条件取其二触发：周线缠论底分型 / 日线 MACD 金叉 / 缩量回踩中枢
- 触发时黄色高亮 + 弹出交易纪律

### 其他
- 系统托盘最小化 + 消息通知
- 每 10 秒刷新行情，每 5 分钟扫描买点，每 30 秒刷新 K 线
- 收盘后（15:05）自动更新止损线

## 环境要求

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python | ≥ 3.10 | 运行环境 |
| PyQt5 | ≥ 5.15.9 | 桌面 GUI |
| akshare | ≥ 1.14.0 | A 股数据源 |
| mplfinance | ≥ 0.12.10 | K 线图绘制 |
| matplotlib | ≥ 3.7.0 | 图表渲染 |
| pandas | ≥ 2.0.0 | 数据处理 |
| numpy | ≥ 1.24.0 | 数值计算 |

## 快速启动

```bash
# 1. 克隆项目
git clone <repo-url>
cd trading-assistant

# 2. 创建虚拟环境（推荐）
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 启动
python main.py
```

首次启动会自动创建 `trading_assistant.db` 数据库和预设分组。

## 交易纪律

编辑 `resources/discipline.txt` 可自定义交易纪律内容。买点信号出现时双击股票即弹出该文本，需勾选"已阅读并遵守"方可关闭。

## 数据来源

行情数据来自 [AKShare](https://github.com/akfamily/akshare) 开源金融数据接口。本项目内置了分 TTL 的智能缓存机制以避免频繁请求被封 IP。
