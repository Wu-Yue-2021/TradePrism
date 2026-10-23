"""组合复盘引擎 —— 跨品种的区间口径聚合

从 irr_analyzer 拆出：后者管单品种的做T识别与现金流生成，本模块管跨品种聚合。
求解器与通用统计（solve_xirr / calc_trade_metrics / preprocess_records）留在
irr_analyzer —— TradeAnalyzer 同样依赖它们，搬过来会形成循环依赖；
calc_bucket_extra 与 TOP_N 只有本模块使用，故随类一起放在这里。
报告渲染见 portfolio_review.py，与本模块完全解耦。
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from irr_analyzer import (
    TradeAnalyzer,
    calc_trade_metrics,
    preprocess_records,
    solve_xirr,
)

TOP_N = 3               # 排行榜与盈/亏最大单的展示数量


def calc_bucket_extra(trades: List[Dict]) -> Dict[str, Any]:
    """
    分桶附加统计：盈利/亏损单的平均持仓天数、单笔均投入、盈利 Top-N / 亏损 Top-N

    仅组合复盘使用，故与 PortfolioAnalyzer 同处一个模块；单品种报告的 metrics
    契约不含这些项（它自带 days 列与贡献度列），两者互不影响。
    """
    if not trades:
        return {'win_days': 0.0, 'loss_days': 0.0, 'avg_investment': 0.0,
                'best_list': [], 'worst_list': []}

    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    return {
        'win_days': sum(t['days'] for t in wins) / len(wins) if wins else 0.0,
        'loss_days': sum(t['days'] for t in losses) / len(losses) if losses else 0.0,
        'avg_investment': sum(t['investment'] for t in trades) / len(trades),
        'best_list': sorted(trades, key=lambda t: t['pnl'], reverse=True)[:TOP_N],
        'worst_list': sorted(trades, key=lambda t: t['pnl'])[:TOP_N],
    }


# ===============================
# 记录查询（模块级纯函数）
# 区间复盘需要"某时点的状态/价格"，只依赖记录序列，与品种和存储无关
# ===============================

def latest_record(records: List[Dict[str, Any]], ts: str, inclusive: bool = True) -> Optional[Dict[str, Any]]:
    """
    取 ts 之前最后一条记录（inclusive=True 时含 ts 时刻本身），无则返回 None

    不依赖记录已排序；同一时刻有多条时取列表中最后出现的一条。
    """
    best = None
    for r in records:
        t = r['ts']
        if t > ts or (not inclusive and t == ts):
            continue
        if best is None or t >= best['ts']:
            best = r
    return best


def state_before(records: List[Dict[str, Any]], ts: str) -> Optional[Dict[str, Any]]:
    """
    **严格早于** ts 的最后一条记录，用于取区间起点之前的持仓状态

    边界取严格小于：区间是闭区间 [start, end]，期初若含 start 时刻本身，恰好落在
    start 的那一刻的记录会被期初与区间内重复计入。注意 DataManager.get_current_state
    取的是 <=，语义不同，不能替代本函数。
    """
    return latest_record(records, ts, inclusive=False)


def resolve_price(records: List[Dict[str, Any]], ts: str) -> Tuple[Optional[float], Optional[str]]:
    """
    ts 时刻（含）之前最近一条记录的价格，返回 (price, actual_ts)；无记录返回 (None, None)

    actual_ts 是该价格实际所属的记录时间，供报告标注"此价取自某日"。
    不跳过任何 side —— RECORD 本就是价格日志，RESET/NEW 的 price 亦是有效市价。
    """
    rec = latest_record(records, ts, inclusive=True)
    if not rec:
        return None, None
    return float(rec['price']), rec['ts']


def _ts_to_dt(ts: str) -> datetime:
    """ts（时间戳字符串） 转 datetime"""
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")


# ===============================
# 组合复盘分析（跨品种的区间口径聚合）
# ===============================

class PortfolioAnalyzer:
    """
    组合复盘分析器 —— 跨品种的区间资金效率分析

    组合 XIRR 不是各品种 XIRR 的平均，而是把所有品种的现金流合并成一条时间序列后重新
    解 NPV(r)=0。资金加权是 XIRR 方程自带的性质：NPV 里每个现金流以自身金额为系数，
    大额长持仓位的贡献天然更大。合并后时间零点无需对齐 —— 所有 t 平移 c 只是给 NPV 乘
    e^(-cu)，不改变根。

    与 TradeAnalyzer 的分工：后者管单品种的做T识别与现金流生成，本类管跨品种聚合；
    两者共用 solve_xirr / calc_trade_metrics / calculate_trade_fee。

    区间口径的关键：期初既有持仓被折叠成一条虚拟 lot 交给 TradeAnalyzer（ts=区间起点、
    price=期初价），于是后续平仓算出的 pnl 天然是区间内盈亏、TWAC 基数天然是期初市值、
    起点现金流由 run() 末尾自动补出——三件事都不需要额外的重定基逻辑。
    """

    def __init__(
        self,
        records_by_symbol: Dict[str, List[Dict[str, Any]]],
        start_ts: str,
        end_ts: str,
        window_days: int = 3,
        price_overrides: Optional[Dict[str, Dict[str, float]]] = None,
        cash_symbol: str = '【现金】',
        cash_scale: float = 100.0,
    ):
        """
        records_by_symbol: {品种: 记录列表}；可含现金品种（按 cash_symbol 识别并另行处理）
        start_ts / end_ts: 统计区间，闭区间 [start, end]
        window_days: 做T判定窗口，透传给 TradeAnalyzer
        price_overrides: {品种: {'open': 价, 'end': 价}}，覆盖自动推断的期初/期末价
        cash_symbol / cash_scale: 现金品种名与份额换算比（份额 = 金额 × cash_scale）
        """
        self.records_by_symbol = records_by_symbol
        self.start_ts = start_ts
        self.end_ts = end_ts
        self.start_dt = _ts_to_dt(start_ts)
        self.end_dt = _ts_to_dt(end_ts)
        self.window_days = window_days
        self.price_overrides = price_overrides or {}
        self.cash_symbol = cash_symbol
        self.cash_scale = cash_scale

        self.period_days = max((self.end_dt - self.start_dt).total_seconds() / 86400.0, 1e-9)
        self.symbols: Dict[str, Dict[str, Any]] = {}
        self.cash: Dict[str, Any] = {}
        self.ranked: List[str] = []

    # ---------- 公开接口 ----------

    def scan_prices(self) -> List[Dict[str, Any]]:
        """
        价格体检表（只读记录，不跑分析）—— 供交互层决定提示哪些品种需要手动覆盖

        has_interval_record=False 表示该品种在区间内没有任何记录，期初价与期末价来自
        同一条记录，区间浮动只能按 0 计——这是**数据缺失**而非"没有波动"，报告应显式
        标注而不是显示 0.00。open_lag_days 是期初价距今的天数，过旧时提示覆盖。
        """
        rows = []
        for symbol, records in self.records_by_symbol.items():
            if symbol == self.cash_symbol or not records:
                continue
            if not self._is_relevant(records):
                continue
            prev, open_price, open_date, end_price, end_date = self._resolve_prices(symbol, records)
            rows.append({
                'symbol': symbol,
                'qty0': int(prev['current_qty']) if prev else 0,
                'open_price': open_price,
                'open_date': open_date,
                # 期初价距今的天数（正 = 价格记录早于区间起点）；手动覆盖价无来源日期
                'open_lag_days': ((self.start_dt - _ts_to_dt(open_date)).total_seconds() / 86400.0
                                  if open_date else None),
                'end_price': end_price,
                'end_date': end_date,
                'has_interval_record': self._has_interval_record(records),
            })
        rows.sort(key=lambda r: r['symbol'])
        return rows

    def run(self) -> Dict[str, Any]:
        """
        执行全组合分析，返回完整结果字典

        纯计算：不碰文件、不打印、不 input，可直接被 CLI 渲染层或 Web API 消费。
        """
        self.symbols = {}
        for symbol, records in self.records_by_symbol.items():
            if symbol == self.cash_symbol or not records:
                continue
            if not self._is_relevant(records):
                continue
            self.symbols[symbol] = self.analyze_symbol(symbol)

        self.cash = self._cash_ctx()
        self.ranked = sorted(self.symbols, key=lambda s: self.symbols[s]['total_pnl'], reverse=True)
        return self._aggregate()

    def analyze_symbol(self, symbol: str) -> Dict[str, Any]:
        """单品种的区间口径结果（公开，便于逐品种核对与单测）"""
        ctx = self._symbol_ctx(symbol)
        a = ctx['analyzer']

        closed = ([dict(t, kind='T', symbol=symbol) for t in a.t_trades]
                  + [dict(t, kind='S', symbol=symbol) for t in a.strategic_trades])
        closed.sort(key=lambda t: t['sell_ts'])

        realized = sum(t['pnl'] for t in closed)
        unrealized = a.unrealized_pnl
        capital_days = self._period_capital_days(ctx)
        twac = capital_days / self.period_days
        exposure = self._open_exposure(ctx)

        return {
            'symbol': symbol,
            'qty0': ctx['qty0'],
            'open_price': ctx['open_price'],
            'open_date': ctx['open_date'],
            'end_price': ctx['end_price'],
            'end_date': ctx['end_date'],
            'has_interval_record': ctx['has_interval_record'],
            'realized': realized,
            'unrealized': unrealized,
            'total_pnl': realized + unrealized,
            'capital_days': capital_days,
            'twac': twac,
            'capital_return': (realized + unrealized) / twac * 100 if twac else 0.0,
            'closed': closed,
            'all_metrics': calc_trade_metrics(closed),
            'all_extra': calc_bucket_extra(closed),
            't_metrics': calc_trade_metrics(a.t_trades),
            's_metrics': calc_trade_metrics(a.strategic_trades),
            't_extra': calc_bucket_extra(a.t_trades),
            's_extra': calc_bucket_extra(a.strategic_trades),
            'exposure': exposure,
            # 与单品种 IRR 界面同口径（不含期初持仓、含未平仓终值），便于对照验证
            'xirr_full': ctx['xirr_full'],
            'xirr_closed_flows': ctx['xirr_closed_flows'],
            'open_mv': ctx['qty0'] * ctx['open_price'] if (ctx['qty0'] and ctx['open_price']) else 0.0,
            'end_mv': exposure['total_end_mv'],
        }

    # ---------- 单品种上下文与解析 ----------

    def _symbol_ctx(self, symbol: str) -> Dict[str, Any]:
        """
        单品种上下文：期初/期末状态与价格、区间内记录、注入期初持仓后的分析器

        期初持仓折叠为**一条**虚拟 lot：期初的多个原始 lot 在期初市值口径下价格被统一
        替换为期初价、起点被钳到区间起点，消耗哪一条结果都一样，故合并无损。
        """
        records = self.records_by_symbol[symbol]
        prev, open_price, open_date, end_price, end_date = self._resolve_prices(symbol, records)
        qty0 = int(prev['current_qty']) if prev else 0
        in_range = [r for r in records if self.start_ts <= r['ts'] <= self.end_ts]

        initial_lots = []
        if qty0 > 0 and open_price:
            initial_lots.append({
                'price': float(open_price),
                'qty': qty0,
                'ts': self.start_dt,
                'buy_fee': 0.0,     # 期初持仓的"买入"并非区间内真实发生，不计费
                'opening': True,    # 供期末敞口拆分识别
            })

        analyzer = TradeAnalyzer(
            preprocess_records(in_range),
            end_price or 0.0,
            symbol,
            self.end_ts,
            self.window_days,
        )

        # 先跑不注入期初持仓的一遍，取与单品种 IRR 界面同口径的 XIRR 及其现金流。
        # 期初持仓若作为"区间起点投入"进入现金流，年化时间轴会失真——那笔钱实际投得
        # 更早，被压成区间长度后年化就错了。run() 会重置全部状态，故先取后跑。
        analyzer.run()
        xirr_full = analyzer.calculate_xirr(exclude_t=True)
        xirr_closed_flows = list(analyzer.strategic_cash_flows[:analyzer.matched_cf_len])

        # 再跑注入期初持仓的一遍，取区间盈亏 / TWAC 基数 / 期末敞口拆分
        analyzer.run(initial_lots=initial_lots)

        return {
            'symbol': symbol,
            'qty0': qty0,
            'open_price': open_price,
            'open_date': open_date,
            'end_price': end_price,
            'end_date': end_date,
            'has_interval_record': bool(in_range),
            'analyzer': analyzer,
            'xirr_full': xirr_full,
            'xirr_closed_flows': xirr_closed_flows,
        }

    def _resolve_prices(self, symbol: str, records: List[Dict[str, Any]]) -> tuple:
        """
        解析期初/期末价：手动覆盖优先，否则从记录推断

        期初价与期初数量**故意取不同的边界**：数量要严格早于区间起点（否则落在起点的
        记录会被期初与区间内重复计入），价格则含起点时刻本身——补录的价格记录正落在
        起点上，用严格小于会取不到而回退到很久以前的成交价。
        """
        prev = state_before(records, self.start_ts)
        ov = self.price_overrides.get(symbol, {})

        if 'open' in ov:
            open_price, open_date = float(ov['open']), None
        else:
            open_price, open_date = resolve_price(records, self.start_ts)

        if 'end' in ov:
            end_price, end_date = float(ov['end']), None
        else:
            end_price, end_date = resolve_price(records, self.end_ts)

        return prev, open_price, open_date, end_price, end_date

    def _has_interval_record(self, records: List[Dict[str, Any]]) -> bool:
        return any(self.start_ts <= r['ts'] <= self.end_ts for r in records)

    def _is_relevant(self, records: List[Dict[str, Any]]) -> bool:
        """区间前有持仓、或区间内有记录 —— 否则该品种与本区间无关"""
        prev = state_before(records, self.start_ts)
        qty0 = int(prev['current_qty']) if prev else 0
        return qty0 > 0 or self._has_interval_record(records)

    # ---------- 区间口径计算 ----------

    def _period_capital_days(self, ctx: Dict[str, Any]) -> float:
        """
        区间内的「资金 × 天数」累加（元·天），TWAC 的分子

        统一规则，无需分底仓/新建：已平仓按 investment × 持有天数（反T 取负，因其资本
        占用方向与正T 相反），期末仍持有按「lot 价×数量 + 买入费」× (期末 − 建仓)。
        之所以能统一，是因为期初持仓已折叠成 ts=区间起点、price=期初价的虚拟 lot，
        它被平仓时与区间内新建仓走同一条路径，investment 天然等于期初市值。
        """
        a = ctx['analyzer']
        cap_days = 0.0

        for t in a.t_trades:
            sign = -1.0 if t.get('t_type', '').strip() == '反T' else 1.0
            cap_days += t['investment'] * t['days'] * sign
        for t in a.strategic_trades:
            cap_days += t['investment'] * t['days']
        for lot in a.strategic_inventory:
            base = lot['price'] * lot['qty'] + (lot.get('buy_fee') or 0.0)
            held = max((self.end_dt - lot['ts']).total_seconds() / 86400.0, 0.0)
            cap_days += base * held

        return cap_days

    def _open_exposure(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """
        期末敞口：按 lot 的 opening 标记拆 [历史遗留底仓] / [本期新建持仓]

        两者的区间浮动基准不同（期初市值 vs 实际含费成本），但都等于
        end_mv − base，故两组合计恒等于 analyzer.unrealized_pnl。
        """
        a = ctx['analyzer']
        symbol = ctx['symbol']
        end_price = ctx['end_price'] or 0.0

        legacy = {'lots': 0, 'qty': 0, 'end_mv': 0.0, 'base': 0.0, 'pnl': 0.0, 'sell_fee': 0.0}
        new = {'lots': 0, 'qty': 0, 'end_mv': 0.0, 'base': 0.0, 'pnl': 0.0, 'sell_fee': 0.0}

        for lot in a.strategic_inventory:
            qty = lot['qty']
            sell_fee = TradeAnalyzer.calculate_trade_fee(end_price, qty, 'SELL', symbol, self.end_dt)
            end_mv = end_price * qty - sell_fee
            base = lot['price'] * qty + a._lot_buy_fee(lot, qty)
            g = legacy if lot.get('opening') else new
            g['lots'] += 1
            g['qty'] += qty
            g['end_mv'] += end_mv
            g['base'] += base
            g['pnl'] += end_mv - base
            g['sell_fee'] += sell_fee

        return {
            'legacy': legacy,
            'new': new,
            'total_end_mv': legacy['end_mv'] + new['end_mv'],
            'holding_sell_fee': legacy['sell_fee'] + new['sell_fee'],
        }

    def _daily_nav_drawdown(self) -> Dict[str, Any]:
        """
        逐日净值曲线的最大回撤（元）

        每日净值 = Σ(各品种持仓量 × 当日价) + 现金余额；价取该日之前最近一条记录，
        故无记录的日子沿用前价、曲线不跳变。

        **出入金已剔除**，且不区分来源（手动现金记录 / 现金 RESET / 交割单同步 /
        编辑级联），统一用恒等式逐日识别：

            当日外部流入 = Δ现金余额 + 证券侧净流入(买含费 − 卖扣费)

        有对应证券交易的现金变化被 sec_net 抵消（属账户内部转移），无对应的即为外部
        资金。累计外部流入从净值中扣掉，其阶跃便不会计入回撤。

        （弃用过的"逐笔已实现 + 末点浮动"口径：浮动只能整体记在末点，会让最大回撤
        退化成「已实现曲线最高点 − 区间总净利」，与曲线形状无关。）
        """
        sec_net = self._security_net_inflow_by_day()

        cash_records = self.records_by_symbol.get(self.cash_symbol) or []
        days = []
        day = self.start_dt.date()
        while day <= self.end_dt.date():
            days.append(day.isoformat())
            day += timedelta(days=1)

        prev_cash = None
        cum_external = 0.0
        peak = None
        peak_day = None
        mdd = 0.0
        mdd_from = mdd_to = None
        for d in days:
            ts = f"{d} 23:59:59"
            nav = 0.0
            for symbol, records in self.records_by_symbol.items():
                if symbol == self.cash_symbol or not records:
                    continue
                rec = latest_record(records, ts)
                if rec:
                    qty = int(rec['current_qty'])
                    if qty > 0:
                        nav += qty * float(rec['price'])

            cash_rec = latest_record(cash_records, ts)
            cash = int(cash_rec['current_qty']) / self.cash_scale if cash_rec else 0.0
            nav += cash

            if prev_cash is not None:
                cum_external += (cash - prev_cash) + sec_net.get(d, 0.0)
            prev_cash = cash

            adjusted = nav - cum_external
            if peak is None or adjusted > peak:
                peak, peak_day = adjusted, d
            if peak - adjusted > mdd:
                mdd, mdd_from, mdd_to = peak - adjusted, peak_day, d
        return {'value': mdd, 'from': mdd_from, 'to': mdd_to}

    def _cash_ctx(self) -> Dict[str, Any]:
        """期初/期末现金余额（现金份额换算：余额 = current_qty / cash_scale）"""
        records = self.records_by_symbol.get(self.cash_symbol) or []
        rec0 = state_before(records, self.start_ts)
        rec1 = latest_record(records, self.end_ts)
        return {
            'open': int(rec0['current_qty']) / self.cash_scale if rec0 else 0.0,
            'end': int(rec1['current_qty']) / self.cash_scale if rec1 else 0.0,
        }

    # ---------- 组合聚合 ----------

    def _aggregate(self) -> Dict[str, Any]:
        """组合层聚合：资产、盈亏、资金效率、指标、现金流、敞口、风控、榜单、分组"""
        syms = list(self.symbols.values())

        open_sec = sum(s['open_mv'] for s in syms)
        end_sec = sum(s['end_mv'] for s in syms)
        open_total = open_sec + self.cash['open']
        end_total = end_sec + self.cash['end']

        realized = sum(s['realized'] for s in syms)
        unrealized = sum(s['unrealized'] for s in syms)
        total_pnl = realized + unrealized

        capital_days = sum(s['capital_days'] for s in syms)
        twac = capital_days / self.period_days

        merged = [t for s in syms for t in s['closed']]
        merged.sort(key=lambda t: t['sell_ts'])

        closed_s_flows = [cf for s in syms for cf in s['xirr_closed_flows']]

        fees_paid = self._fees_paid()
        folds = self._fold_groups(syms, self.ranked)

        # 外部流入 = Δ现金 + 证券侧净流入(买含费 − 卖扣费)：买卖对现金的影响被第二项抵消，
        # 余下的即无对应证券交易的现金变化（出入金），是实测值而非估计
        external = ((self.cash['end'] - self.cash['open'])
                    + sum(self._security_net_inflow_by_day().values()))

        exposure = {'legacy': {'lots': 0, 'qty': 0, 'end_mv': 0.0, 'base': 0.0, 'pnl': 0.0, 'sell_fee': 0.0},
                    'new': {'lots': 0, 'qty': 0, 'end_mv': 0.0, 'base': 0.0, 'pnl': 0.0, 'sell_fee': 0.0},
                    'total_end_mv': end_sec}
        for s in syms:
            for key in ('legacy', 'new'):
                for field, value in s['exposure'][key].items():
                    exposure[key][field] += value
        exposure['holding_sell_fee'] = exposure['legacy']['sell_fee'] + exposure['new']['sell_fee']

        return {
            'period': {
                'start': self.start_ts,
                'end': self.end_ts,
                'days': self.period_days,
                'window_days': self.window_days,
                'symbol_count': len(syms),
            },
            'assets': {
                'open_total': open_total, 'open_sec': open_sec, 'open_cash': self.cash['open'],
                'end_total': end_total, 'end_sec': end_sec, 'end_cash': self.cash['end'],
            },
            'pnl': {
                'realized': realized,
                'unrealized': unrealized,
                'total': total_pnl,
                'delta_assets': end_total - open_total,
                # 差额 = 资产变动 − 已实现 − 浮动：外部资金进出、NEW 建仓未同步现金、
                # 费用按工具费率估算的口径差等。显式摆出，避免把"净利"当成账户真实变化
                'reconcile_gap': (end_total - open_total) - total_pnl,
            },
            'returns': {
                'twac': twac,
                'on_open_assets': total_pnl / open_total * 100 if open_total else 0.0,
                'on_twac': total_pnl / twac * 100 if twac else 0.0,
            },
            'metrics': {
                'all': calc_trade_metrics(merged, total_pnl),
                't': calc_trade_metrics([t for t in merged if t['kind'] == 'T'], total_pnl),
                's': calc_trade_metrics([t for t in merged if t['kind'] == 'S'], total_pnl),
            },
            'extra': {
                't': calc_bucket_extra([t for t in merged if t['kind'] == 'T']),
                's': calc_bucket_extra([t for t in merged if t['kind'] == 'S']),
            },
            'fees': {
                'paid': fees_paid,
                'holding_est': sum(s['exposure']['holding_sell_fee'] for s in syms),
            },
            'external': {'external': external},
            'xirr': {
                'closed_s': solve_xirr(closed_s_flows),
            },
            'exposure': exposure,
            'cash': dict(self.cash, cover_ratio=(self.cash['end'] / end_total * 100) if end_total else 0.0),
            'risk': {
                'max_loss_streak': self._loss_streak(merged),
                'nav_drawdown': self._daily_nav_drawdown(),
                'worst_trade': min(merged, key=lambda t: t['pnl']) if merged else None,
                'best_trade': max(merged, key=lambda t: t['pnl']) if merged else None,
            },
            'top': [self.symbols[s] for s in self.ranked[:TOP_N]],
            'bottom': [self.symbols[s] for s in self.ranked[-TOP_N:][::-1]]
                      if len(self.ranked) > TOP_N else [],
            'folds': folds,
            'symbols': self.symbols,
        }

    def _security_net_inflow_by_day(self) -> Dict[str, float]:
        """
        区间内证券侧净流入，按日聚合：{日期: Σ买入(含费) − Σ卖出(扣费)}

        只统计非现金品种的真实交易。供外部资金恒等式（全区间求和）与逐日净值
        （当日出入金识别）共用——两处口径必须同源，否则回撤与对账会各说各话。
        """
        net: Dict[str, float] = {}
        for symbol, records in self.records_by_symbol.items():
            if symbol == self.cash_symbol:
                continue
            for r in records:
                if not (self.start_ts <= r['ts'] <= self.end_ts):
                    continue
                qty = abs(int(r['delta_qty']))
                amount = float(r['price']) * qty
                fee = TradeAnalyzer.calculate_trade_fee(
                    float(r['price']), qty, r['side'], symbol, _ts_to_dt(r['ts']))
                if r['side'] in ('BUY', 'NEW'):
                    delta = amount + fee
                elif r['side'] == 'SELL':
                    delta = -(amount - fee)
                else:
                    continue
                day = r['ts'][:10]
                net[day] = net.get(day, 0.0) + delta
        return net

    def _fees_paid(self) -> float:
        """区间内全部真实交易（NEW/BUY/SELL）按工具费率公式估算的手续费合计"""
        total = 0.0
        for symbol, records in self.records_by_symbol.items():
            if symbol == self.cash_symbol:
                continue
            for r in records:
                if not (self.start_ts <= r['ts'] <= self.end_ts):
                    continue
                if r['side'] in ('NEW', 'BUY', 'SELL'):
                    total += TradeAnalyzer.calculate_trade_fee(
                        float(r['price']), abs(int(r['delta_qty'])), r['side'],
                        symbol, _ts_to_dt(r['ts']))
        return total

    @staticmethod
    def _loss_streak(closed: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """最长连续亏损（按平仓时间排序的列表上扫描），无亏损返回 None"""
        best, cur = [], []
        for t in closed:
            if t['pnl'] <= 0:
                cur.append(t)
            else:
                if len(cur) > len(best):
                    best = cur
                cur = []
        if len(cur) > len(best):
            best = cur
        if not best:
            return None
        return {
            'count': len(best),
            'from': best[0]['sell_ts'][:10],
            'to': best[-1]['sell_ts'][:10],
            'sum': sum(t['pnl'] for t in best),
        }

    @staticmethod
    def _fold_groups(syms: List[Dict[str, Any]],
                     ranked: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        分组：

        - no_interval_record：区间内无记录 ⇒ 期初价与期末价同源，区间浮动只能按 0 计，
          属**数据缺失**而非"无波动"，由报告顶部提示一行，不逐条列出
        - others：既未进 Top/Bottom 榜、也不属上者的其余品种，逐条简略一行。
          **不设金额阈值**——一旦设了阈值，就会出现"没上榜又超过阈值"的品种从报告里
          凭空消失（补录价格后盈亏越过分界线即触发）
        """
        no_rec = [s for s in syms if not s['has_interval_record']]
        shown = set()
        if ranked:
            shown = set(ranked[:TOP_N]) | (set(ranked[-TOP_N:]) if len(ranked) > TOP_N else set())
        others = [s for s in syms if s['has_interval_record'] and s['symbol'] not in shown]
        others.sort(key=lambda s: s['total_pnl'], reverse=True)
        return {
            'no_interval_record': no_rec,
            'no_interval_record_mv': sum(s['open_mv'] for s in no_rec),
            'others': others,
        }

