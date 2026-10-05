# CLAUDE.md

## 项目概述

个人金融工具集，中文界面，用于股票/基金交易记录管理、投资收益率分析和K线形态分析。

**核心功能**：
- 交易记录管理（CRUD、成本计算、交割单导入）
- IRR 内部收益率分析（LIFO 匹配、XIRR 计算）
- K线形态分析（开收关系、量价特征、影线分析）

## 文件结构

| 文件 | 功能 |
|------|------|
| `trading_record_manager.py` | 主程序，交易记录管理 CLI |
| `irr_analyzer.py` | IRR 分析引擎，可独立导入 |
| `candle_stick_analyzer.py` | K线形态分析工具 |
| `5min_volume_analyzer.py` | 分时量能分析工具 |
| `data_utils.py` | K线数据处理工具模块 |
| `stock_calculator.py` | 股票计算工具 |
| `data/trades.json` | 交易记录数据存储 |
| `data/backup_YYYY-MM-DD/` | 自动备份目录 |
| `data/export/` | 导出文件目录 |
| `huatai/` | 华泰导出的日线数据目录 |
| `docs/` | 专题文档（XIRR 非均匀网格法推导、做T匹配算法） |
| `code_tmp/xirr/` | XIRR 网格定参的分析/验证脚本与采样数据（非生产代码） |

## 常用命令

**注意：Claude 测试时一定要切换字符集为 UTF-8 以避免中文输出乱码。**

```bash
# 启动交易记录管理器
python trading_record_manager.py

# 启动 K线分析器（带文件参数）
python candle_stick_analyzer.py huatai/300738_日线.txt

# 分时量能分析
python 5min_volume_analyzer.py <文件> [天数] [-r]

# 避免 Python 中文输出乱码（PowerShell 5.1 控制台默认 GBK，Python 输出 UTF-8）
$env:PYTHONIOENCODING='utf-8'; python xxx
# 备选：切换控制台代码页
chcp 65001

# 运行临时测试代码（PowerShell 5.1 下 python -c 的引号会被吞，改用 stdin 管道）
@'
print("test")
'@ | python -

# 查找 powershell 配置文件位置（用于配置 powershell 命令别名）
echo $PROFILE.CurrentUserCurrentHost

```

## 核心概念

### 做T识别算法：动态堆匹配

本工具使用**动态堆匹配算法**识别做T交易：
- 全部交易读入数组，双向链表维护相邻关系
- 最小堆按时间距离排序，贪心匹配相邻反向对
- 匹配后更新链表，检查新形成的相邻对
- 超时未匹配交易进入战略持仓（LIFO 栈）
- `T_allow_cross` 参数控制是否允许交叉匹配

### XIRR 求解算法：对数域扫描 + 牛顿细化 + 二分兜底

求 `NPV(r)=0` 的全部实根 `[(r, u), ...]`（r 年化，`u=ln(1+r)`），三段式：

- **扫描**：四层非均匀网格（中间密、外围疏，边界 ±`XIRR_SCAN_LIMIT`）定位 NPV 变号区间；参数由 50000 组模拟样本标定
- **细化**：阻尼牛顿法求根，未收敛时二分兜底
- **多解**：现金流多次变号时方程存在多解，返回全部实根并在报告警示
- **现金流只在匹配时记录**：避免未匹配交易产生幽灵现金流；期末未匹配 BUY 补录买入流 + 终值虚拟卖出；反T `capital_days` 取负

### 成本计算方法：调整成本法

本工具使用**调整成本法**（非加权移动平均法）。卖出时按当前价格从总成本中扣除卖出份额，计算新的平均成本。

### 操作类型

| 类型 | 说明 |
|------|------|
| `BUY` | 买入操作 |
| `SELL` | 卖出操作 |
| `NEW` | 新建品种（首次记录） |
| `RESET` | 重置持仓/成本/价格 |
| `RECORD` | 记录价格（不交易） |

### 现金记录同步

导入交割单时自动同步现金记录：
- 股票 BUY → 现金 SELL（现金减少）
- 股票 SELL → 现金 BUY（现金增加）
- 现金价格固定为 0.01，份额 = 100 × |金额|

## 数据结构

### trades.json 结构

```json
[
  {
    "symbol": "AAPL",
    "records": [
      {
        "ts": "2026-01-26 19:13:37",
        "side": "BUY",
        "price": "150.000",
        "delta_qty": 100,
        "current_qty": 100,
        "avg_cost_after": "150.0000"
      }
    ]
  }
]
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `ts` | string | 时间戳，格式 `YYYY-MM-DD HH:MM:SS` |
| `side` | string | 操作类型 |
| `price` | string | 成交价格（字符串存储避免精度问题） |
| `delta_qty` | int | 变动份额（正=买入，负=卖出） |
| `current_qty` | int | 变动后持有份额 |
| `avg_cost_after` | string | 变动后平均成本 |

### 华泰日线数据格式

```
300738 奥飞数据 日线 不复权
日期 开盘 最高 最低 收盘 成交量 成交额
01/09/2026 21.20 23.65 21.13 22.85 158041090 3574012900
```
- 编码：GBK
- 日期格式：MM/DD/YYYY


## 核心模块

### Trading Record Manager

| 类/函数 | 功能 |
|---------|------|
| `DataManager` | 数据 CRUD 操作 |
| `DataManager.rename_symbol()` | 重命名品种 |
| `DataManager.add_cash_record()` | 添加现金记录 |
| `DataManager.delete_cash_record()` | 删除现金记录（容差匹配，考虑手续费） |
| `DataManager.cascade_update_cash_records()` | 级联更新后续记录 |
| `DataManager.delete_trades_batch()` | 批量删除记录 |
| `calculate_new_state()` | 计算变动后状态 |
| `interactive_import_statement()` | 交互式导入交割单 |
| `analyze_symbol_irr()` | IRR 分析入口 |
| `irr_analysis_interactive()` | IRR 分析交互界面 |
| `Display.print_price_sensitivity_table()` | 价格波动敏感性表格 |
| `Display.print_records(color_hierarchy)` | 打印记录，含费用列（NEW/BUY/SELL 计算，其余显示 "-"）；开启后做T降色 + 配对序号 |
| `Display._build_match_info()` | 复用 IRR 分析器 `TradeAnalyzer.run()` 按品种识别做T/战略配对，返回（纯做T记录集合, 配对位置映射），供 print_records 颜色层次 |
| `ensure_weekly_backup()` | 每周自动备份 |
| `analyze_position_changes()` | 持仓历史分析 |

### Trading Record Manager 主要配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `DATA_DIR` | `./data` | 数据目录 |
| `EXPORT_DIR` | `./data/export` | 导出目录 |
| `BACKUP_INTERVAL_DAYS` | `7` | 自动备份间隔天数 |
| `PRACTICE` | `True` | 是否按100股取整 |
| `CASH_SYMBOL` | `【现金】` | 现金品种名称 |
| `CASH_PRICE` | `0.01` | 现金固定价格 |
| ANSI/DARK 颜色常量 | — | BLUE/RED/GREEN/YELLOW/CYAN/GREY/RESET 及降色映射 `DARK_COLOR`（做T降色显示用） |

### IRR Analyzer

**注意**：反T（先卖后买）的时间方向、资本占用等与正T相反，改动时留意。

| 类/函数 | 功能 |
|---------|------|
| `TradeAnalyzer` | 动态堆匹配识别做T + LIFO 匹配 + XIRR 计算 |
| `TradeAnalyzer.run()` | 执行分析，分类 t_trades / strategic_trades |
| `TradeAnalyzer.calculate_xirr(exclude_t=True)` | 求 XIRR 全部实根 `[(r, u), ...]`（r 年化、u=ln(1+r)）；对数域扫描 + 阻尼牛顿 + 二分兜底 |
| `TradeAnalyzer._xirr_scan_grid()` | 四层非均匀扫描网格；细化与兜底见 `_xirr_newton()` / `_xirr_bisect()` |
| `TradeAnalyzer.get_t_metrics()` | 做T交易统计（胜率、盈亏比、期望值） |
| `TradeAnalyzer.get_strategic_metrics()` | 战略交易统计 |
| `TradeAnalyzer.calculate_trade_fee()` | 手续费计算静态入口（区分基金/股票，含过户费/印花税，按交易时间分档；实例 `_calculate_trade_fee` 一行转发） |
| `TradeAnalyzer.is_fund()` | 判断是否基金静态方法（价格<5 或含ETF） |
| `preprocess_records()` | 时间筛选 + 过滤 RESET/RECORD |
| `analyze_and_print_irr()` | 格式化输出报告（含做T/战略分类） |

### IRR Analyzer 手续费配置

`calculate_trade_fee()`（静态，实例 `_calculate_trade_fee` 转发）按 `is_fund()` 分基金（`FUND_*`）/股票（`STOCK_*`）两组常量计算，股票另加过户费、卖出印花税。费率按交易时间 `ts` 以 `FEE_CUTOVER_DATE`（`2026-07-11`）为界切换旧档 / `_DB`（降费）档；`ts` 缺失或 `force_HT=True` 用旧档。具体数值见 `irr_analyzer.py` 顶部常量（README_CN.md 保留完整对照表）。

**XIRR 求解配置**（`irr_analyzer.py` 顶部常量）

| 配置项 | 值 | 说明 |
|--------|-----|------|
| `XIRR_SCAN_LIMIT` | `150.0` | 扫描边界 ±150，覆盖创业板 20cm（-81/+67）与新股首日（+133） |
| `XIRR_SCAN_LAYERS` | `(3,0.019) (10,0.17) (80,0.53) (150,18)` | 四层非均匀网格「(外边界, 步长)」，由内到外逐级放大 |

### Candlestick Analyzer

| 类/函数 | 功能 |
|---------|------|
| `CandleStickAnalyzer` | K线分析主类 |
| `analyze_open_close_patterns()` | 开收关系分析 |
| `analyze_big_candles()` | 大阳线/大阴线分析 |
| `analyze_volume_patterns()` | 量价特征分析 |
| `analyze_gap_patterns()` | 跳空分析 |
| `analyze_shadow_patterns()` | 影线分析 |

### Intraday Volume Analyzer (5min_volume_analyzer.py)

| 类/函数 | 功能 |
|---------|------|
| `IntradayVolumeAnalyzer` | 分时量能分析主类 |
| `load_data()` | 加载华泰分钟线数据 |
| `analyze()` | 计算相对量能比例和涨跌幅 |
| `print_table()` | 打印分时量能表格 |
| `plot_charts()` | 生成可视化图表 |

## 代码规范

1. **数据存储**：价格使用字符串存储避免浮点精度问题
2. **时间格式**：统一使用 `YYYY-MM-DD HH:MM:SS`
3. **份额单位**：股数按100股取整（PRACTICE=True时）
4. **中文字符格式化**（重要）：
   - 中文显示宽度是2，英文是1，但 `len()` 都算1
   - **格式化宽度 = 目标显示宽度 - 中文字符数**
   - 例：`{'价格波动':^6}` — 4个中文显示宽度8，格式化宽度6，系统会再补2个空格，总显示宽度10；所以如果:^10的英文/数据，中文格式化宽度就是6
   - 居中对齐用 `:^N`，比左对齐 `:<N` 或右对齐 `:>N` 更适合表格输出
   - **每次输出格式化字符串时必须确认：该列是中文还是英文，有多少中文字符**

## 相关文档

- [XIRR 网格法推导与定参](docs/xirr_grid_method_derivation.md) — 四层网格的理论推导与 50000 组模拟实证
- [做T匹配算法](docs/t_trade_matching_algorithm.md)
- [对外精简版 README](README.md) — GitHub 首页：工具表、算法亮点、配置位置表、开发历程
- [项目概览](README_CN.md)
