# TradePrism | 个人股票/基金交易记账与波段复盘系统

一款专为波段交易与精细化复盘设计的个人股票/基金交易记录管理与投资分析工具集：**交易流水管理 → 成本核算 → 做T识别 → XIRR 收益率分析 → K线/分时形态统计**，纯 Python 实现，CLI 交互。

特点：自动将多笔复杂的买卖记录拆解为“战略交易”、“短线做T”与“当前持仓”，智能匹配每一笔交易的对应关系并计算 ROI 和 ARR 等指标, 最终综合所有现金流计算 IRR（内部收益率），相比传统的最大成本收益率，不会因为资金频繁进出导致收益率严重失真。
 - 对于中长线交易，采用先进后出（LIFO）而非同花顺等软件常用的先进先出（FIFO），更符合波段投资者的心理和实际操作习惯。
 - 对于短线/做T交易，多次优化算法严格实现最近交易匹配，允许交叉匹配，正确追踪所有交易的盈亏情况。
 - 自动同步现金变化，支持重建任意日期的总资产、持仓市值与仓位比例，动态追溯每日交易情况与资产变化。

> 完整功能文档见 [README_CN.md](README_CN.md)。本仓库仅公开代码与模拟示例数据，真实交易数据（`data/trades.json`、备份、导出）不入库。

## 工具一览

| 工具 | 启动方式 | 功能 | 依赖 |
|------|---------|------|------|
| [trading_record_manager.py](trading_record_manager.py) | `python trading_record_manager.py` | 主程序：交易记录 CRUD、交割单导入、现金记录自动同步、调整成本法均价、每周自动备份、价格敏感性/持仓历史分析 | 无（纯标准库） |
| [irr_analyzer.py](irr_analyzer.py) | 由主程序菜单调用 | IRR 分析引擎：做T识别、LIFO 战略匹配、XIRR 求解、手续费分档、胜率/盈亏比/期望值统计 | 无 |
| [portfolio_analyzer.py](portfolio_analyzer.py) | 由主程序菜单 `r` 调用 | 组合复盘引擎：跨品种区间聚合、组合 XIRR、TWAC、敞口拆分、逐日净值回撤 | 无 |
| [portfolio_review.py](portfolio_review.py) | 同上 | 组合复盘报告渲染：四节版式、中文宽度对齐、分组折叠 | 无 |
| [candle_stick_analyzer.py](candle_stick_analyzer.py) | `python candle_stick_analyzer.py <日线文件>` | K线形态统计：开收关系、大阳/阴线后续表现、量价配合、跳空、影线 | pandas, numpy |
| [5min_volume_analyzer.py](5min_volume_analyzer.py) | `python 5min_volume_analyzer.py <分钟文件> [天数] [-r]` | 分时量能：5分钟线相对量能表格 + 图表 | pandas, numpy, matplotlib |
| [stock_calculator.py](stock_calculator.py) | `python stock_calculator.py` | 早期简易版：调仓均价计算 | 无 |

**运行环境**：Python 3.9+，Windows 终端建议先执行 `chcp 65001` 或将 `PYTHONIOENCODING=utf-8`（避免中文乱码）。

## 核心算法与功能亮点

### 1. 做T识别：动态堆匹配

不用滑动窗口，而是把单品种全部交易一次性读入，**双向链表维护相邻关系 + 最小堆按时间距离贪心匹配相邻反向对**：

- 匹配成功后更新链表，检查新形成的相邻对能否继续配对（离线全局视角，避免窗口边界决策）
- 超时未匹配的交易进入战略持仓（LIFO 栈）
- 支持正T（先买后卖）与反T（先卖后买，资本占用方向相反）
- `T_allow_cross` 开关控制是否允许交叉匹配；买卖间隔 `window_days`（默认 3 天）划分做T/战略交易

详见 [docs/t_trade_matching_algorithm.md](docs/t_trade_matching_algorithm.md)。

### 2. XIRR 求解：对数域扫描 + 阻尼牛顿 + 二分兜底

求 `NPV(r) = 0` 的全部实根，年化收益率直接可比：

- 在 `u = ln(1+r)` 对数域扫描（r 下界 -100% 为开区间，直接迭代 r 易越界抛错）
- **四层非均匀网格**定位变号区间（中间密、外围疏，边界 ±150），网格参数由 50000 组模拟样本标定
- 阻尼牛顿细化求根，未收敛时二分兜底；现金流多次变号时返回全部实根并警示多解

推导与定参实证见 [docs/xirr_grid_method_derivation.md](docs/xirr_grid_method_derivation.md)。

### 3. 双轨账本：资产与现金流的动态镜像

交割单导入时，「【现金】」品种自动生成镜像流水：股票买入 → 现金 SELL（资金减少）、股票卖出 → 现金 BUY（资金回笼），现金固定价格 0.01、份额 = 100 × |发生金额|（即现金市值恒等于真实资金余额）；删除/修改记录时级联更新，保证时间线一致。

这份"股票 + 现金"全视角账本是后续分析的基础设施：

- **历史切片分析**：重建任意日期区间的总资产 / 股票市值 / 现金 / 仓位比例（`analyze_position_changes`），动态追踪仓位变化
- **持仓分析（规划中）**：按品种×日期还原持仓演化
- **AI 分析（规划中）**：为复盘/归因分析提供完整的交易与资金流输入

## 计算说明

- **调整成本法（非加权移动平均）**：卖出时按 `卖出数量 × 卖出价` 从总成本中扣减，剩余持仓均价随之改变。符合"落袋为安"的直觉，便于把成本摊薄到目标价位，但高价卖出可能把均价压得极低。
- **现金流只在匹配时记录**：做T配对、战略 LIFO 配对、期末未匹配 BUY 补录"买入流 + 终值虚拟卖出"三种场景才产生现金流，避免不匹配交易造成幽灵现金流；分析区间起点无法匹配的 SELL 直接跳过；反T 的 `capital_days` 取负。
- **XIRR 默认不包含做T交易**（`exclude_t=True`）：做T不是可无限复利的独立策略，因此报告的 XIRR 默认只计入战略买卖 + 期末市值，衡量底仓投资效率；做T的贡献（手续费后净盈亏、胜率、盈亏比）单独统计。

## 快速开始

**方式一：用示例数据体验**（推荐）

```bash
git clone https://github.com/Wu-Yue-2021/TradePrism.git
cd TradePrism
# Windows 复制示例数据
copy data\trades_example.json data\trades.json
# Linux/macOS 复制示例数据
cp data/trades_example.json data/trades.json

python trading_record_manager.py
```

示例数据（均为虚构品种"示例股份/示例ETF"）包含正T、反T、战略加减仓与价格记录，进主菜单查看记录列表（做T配对降色显示 + 配对序号），再做 IRR 分析即可看到做T统计与 XIRR 报告。

**方式二：手动记账** — 启动主程序后按菜单提示增删改记录、计算调仓、查看分析。

**方式三：导入交割单** — 主菜单选择交割单导入，支持多品种混合，导入时自动同步【现金】品种流水。（目前已适配多数基于同花顺底层的券商 PC 端交割单）

## 主要配置项

| 配置项 | 默认值 | 位置 | 说明 |
|--------|--------|------|------|
| `DATA_DIR` / `FILE` | `./data` / `data/trades.json` | `trading_record_manager.py` 顶部常量区 | 数据目录与主数据文件 |
| `PRACTICE` | `True` | `trading_record_manager.py` 顶部常量区 | 股数按 100 取整 |
| `BACKUP_INTERVAL_DAYS` | `7` | `trading_record_manager.py` 顶部常量区 | 每周自动备份间隔 |
| `CASH_SYMBOL` / `CASH_PRICE` | `【现金】` / `0.01` | `trading_record_manager.py` 顶部常量区 | 现金品种名与固定价 |
| `window_days` | `3` | `irr_analyzer.py` `TradeAnalyzer` 构造参数默认值 | 做T/战略交易的间隔分界（天） |
| 费率常量 `FUND_*` / `STOCK_*` | 见 [README_CN.md](README_CN.md) | `irr_analyzer.py` 顶部常量区 | 佣金（含最低费）/ 过户费 / 卖出印花税，基金（价格<5 或含 ETF）与股票自动区分，按 `FEE_CUTOVER_DATE` 切换新旧档 |
| `FEE_CUTOVER_DATE` | `2026-07-11` | `irr_analyzer.py` 顶部常量区 | 券商降费分档切换日 |
| `XIRR_SCAN_LIMIT` / `XIRR_SCAN_LAYERS` | `150` / 四层网格 | `irr_analyzer.py` 顶部常量区 | XIRR 扫描边界与网格密度 |

费率明细、颜色常量等完整配置表见 [README_CN.md](README_CN.md)。K线工具目前读取华泰证券客户端导出的日线/分钟线文本（GBK 编码，样例见 `huatai/`）。

## 风险提示与免责声明

- 本项目为**个人记账与复盘工具**，输出仅供学习参考，**不构成任何投资建议**。
- 手续费率、降费切换日等按个人券商定制，直接套用会导致费用计算出现偏差，请自行核对常量后再使用。
- `trades_example.json` 中品种与价格均为虚构，仅用于演示；真实持仓数据默认不入 git（见 `.gitignore`）。
- K线/分钟数据依赖券商客户端手动导出，非实时数据源。
- 调整成本法与 XIRR 口径为作者自定义（区别于通用会计口径），解读结果前建议先读"计算说明"。

## 开发历程

| 时间 | 功能 | 
|------|--------|
| 起源 | 由2025年夏开发的单文件调仓计算器 `stock_calculator.py`（算目标均价）演进为 `trading_record_manager.py`；流水数据通过交割单回溯导入 |
| 2026-01～02 | 调整成本法、交割单导入与【现金】自动同步、CSV/JSON 导出等基础功能成型；irr_analyzer.py 并入（XIRR 求解 + 贡献度统计）；批量删除 |
| 2026-03 | 分时量能分析 5min_volume_analyzer.py、K线形态分析 candle_stick_analyzer.py 与数据工具 data_utils.py |
| 2026-04 | IRR 支持指定分析区间终点；首次引入做T识别（滑动窗口版）；价格波动敏感性表格 |
| 2026-05 | 每周自动备份； IRR 报告计算手续费并计入盈亏 |
| 2026-06 | 做T识别重写为**动态堆匹配**：链表 + 最小堆贪心，支持反T与交叉匹配开关 |
| 2026-08 | IRR 报告与记录列表增加单独手续费列（费率按券商协议分档）；做T配对降色 + 操作列配对序号显示 |
| 2026-10 | **XIRR 求解器整体重写**：对数域四层非均匀网格 + 阻尼牛顿 + 二分兜底，50000 组模拟样本定参，支持多解；仓库开源发布（TradePrism），附演示数据与对外 README |
| 2026-10 | **组合复盘**：跨品种区间报告（组合 XIRR 用现金流合并、TWAC 资金效率、策略拆解、标的排名、行为风控）；求解器与指标统计提为模块级纯函数供组合层复用 |

后续方向：持仓/清仓分析、Web Dashboard、行情自动同步（见 [README_CN.md](README_CN.md) 待办事项）。

## License

个人项目，暂不限转载学习；如引用请注明出处。