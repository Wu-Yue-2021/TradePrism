# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a collection of Chinese-language financial tools for:
- **Trading record management** - Tracking stock/fund positions, calculating cost averages
- **IRR analysis** - Analyzing investment performance via Internal Rate of Return
- **K-line pattern analysis** - Analyzing stock candlestick patterns and volume characteristics

## Tools Overview

### 1. Trading Record Manager ([trading_record_manager.py](trading_record_manager.py))

Main CLI application for managing trading records.

**Running:**
```bash
python trading_record_manager.py
# or batch mode
run_input.bat
```

**Features:**
- Trade record CRUD operations
- Cost averaging calculations using "adjusted cost method" (调整成本法)
- Filter/export functionality (CSV, JSON)
- Data persistence via JSON in `data/trades.json`
- Cash record auto-sync (现金记录自动同步)
- Batch delete records (批量删除)

### 2. IRR Analyzer ([irr_analyzer.py](irr_analyzer.py))

IRR analysis engine for calculating investment returns.

**Features:**
- `TradeAnalyzer` class: LIFO trade matching with XIRR calculation via Newton's method
- `preprocess_records()`: Handles time filtering and RESET operations by inserting virtual trades
- `print_irr_report()`: Formatted report showing closed trades, open positions, and contribution percentages

### 3. Candlestick Pattern Analyzer ([candle_stick_analyzer.py](candle_stick_analyzer.py))

K-line形态分析工具 - Analyzes candlestick patterns and volume.

**Running:**
```bash
python candle_stick_analyzer.py
```

**Features:**
- Import data from Huatai (华泰) exported daily K-line files (GBK encoded)
- Open-close pattern analysis: 低开高走/低开低走/高开高走/高开低走
- Big candle analysis with configurable threshold (default ±5%)
  - Next day performance
  - Consecutive up/down days after big candles
- Volume analysis: 放量/缩量 with price movement correlation
- Gap analysis: 跳空高开/跳空低开
- Shadow analysis: 上影线/下影线 ratio relative to body

**Data format:**
```
300738 奥飞数据 日线 不复权
日期 开盘 最高 最低 收盘 成交量 成交额
01/09/2026 21.20 23.65 21.13 22.85 158041090 3574012900
...
```

### 4. Stock Calculator ([stock_calculator.py](stock_calculator.py))

Utility calculator for stock-related computations.

## Architecture

### Operation Types

- `BUY`: Buy shares (positive delta_qty)
- `SELL`: Sell shares (negative delta_qty)
- `RESET`: Reset position/cost/price (inserts virtual trades for IRR continuity)
- `NEW`: Initialize a new symbol's first record
- `RECORD`: Record price without trading

### Cost Method Note

This tool uses **adjusted cost method** (调整成本法), not weighted moving average. When selling, the cost is adjusted by the current sell price, not the original cost basis. This can lead to scenarios where target prices require extreme quantities (including short positions) to achieve.

### Data Storage

- Trade records: `data/trades.json`
- Exports: `data/export/`
- Backup: `data/trades_backup.json`

JSON structure:
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

### IRR Analysis

The IRR analyzer uses LIFO matching and calculates annualized returns:
- Closed trades: Match sells against most recent buys
- Virtual trades: Inserted at midpoint between RESET and previous trade
- XIRR: Solved via Newton-Raphson method in `calculate_xirr()`
- Contribution = (trade PnL / total PnL) × 100%

## Configuration

### Trading Record Manager
- `DATA_DIR`: Set in `trading_record_manager.py` line 17
- `PRACTICE`: Boolean flag (line 24) - when True, rounds quantities to 100-share lots

### Candlestick Pattern Analyzer
- Dependencies: pandas, numpy (install via `pip install pandas numpy`)
- Data source: Huatai (华泰) exported daily K-line files in `huatai/` directory
- Encoding: GBK for input files

## Todo List: 2026-03-18

### Trading Record Manager
- **严重问题**：IRR分析期末时间不为现在时，依然用现在时间算，而且没有参数传进去还不好改 @ function: print_irr_report
- IRR分析加入做T交易过滤和分析的功能 @ function: analyze_symbol_irr & print_irr_report
- 为全体持仓与已请仓进行分析（以前跟AI聊过方案）
- csv/json导入功能（用处不大，完整性考虑）
- 计算中必要的数据类型改成Decimal
- get_current_state函数有一个隐含bug，如果是修改，按时间过滤会get到他自己这条记录；新增没问题
- 合并操作菜单的1和2，并且每次多输出几个相邻阈值的价格，然后加入做T模拟（下一条）
- 由于加入导入交割单后原来的手动计算功能一般都用来做T，所以准备加入暂存、确认保存的功能
- 加入批量删除
- 构想：做一个分时量能分析工具，以每日第一根5分钟线为基准计算每根线的量能比例，输出表格，最好再输出两张图（第一根量横柱状图，相对量能折线图叠加横柱状图；纵坐标日期横坐标时间）
- 当前状态加入最后一次记录时间




### Candlestick Pattern Analyzer
- (New tool - potential enhancements)
  - More pattern recognition (锤子线, 十字星, etc.)
  - Multi-timeframe analysis
  - Pattern success rate backtesting
  - Export analysis results to CSV/JSON
