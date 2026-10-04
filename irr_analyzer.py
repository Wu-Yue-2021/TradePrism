"""
IRR 分析器模块
可直接被其他工具导入使用
"""
import math
from datetime import datetime
from typing import List, Dict, Any, Optional


FUND_PRICE_THRESHOLD = 5 # 基金价格阈值
FUND_COMMISSION_RATE = 1 / 10000 # 基金佣金率
FUND_COMMISSION_RATE_DB = 0.6 / 10000
FUND_MIN_FEE = 0.1 # 基金最低手续费（元）
FUND_MIN_FEE_DB = 0.2
STOCK_COMMISSION_RATE = 1.15 / 10000 # 股票佣金率
STOCK_COMMISSION_RATE_DB = 0.841 / 10000
STOCK_MIN_COMMISSION = 5 # 股票最低手续费（元）
STOCK_MIN_COMMISSION_DB = 0.1
STOCK_TRANSFER_FEE = 2 / 10000 # 股票过户费（元）
STOCK_STAMP_DUTY_RATE = 5 / 10000 # 股票印花税率（仅卖出）
FEE_CUTOVER_DATE = datetime(2026, 7, 11) # 费率分档切换日（之前用旧费率，之后用 _DB 降费档）
XIRR_SCAN_LIMIT = 150.0 # 扫描总边界 ±150：覆盖创业板 20cm（-81/+67）与核准制新股首日（+133）
XIRR_SCAN_LAYERS = (    # 四层非均匀网格 (该层外边界, 该层步长)，由内到外步长逐级放大；步长按可行值向下取整留余量
    (3.0, 0.019),       # 间距最小的根对全部落在 |u| ≤ 3，此层最密
    (10.0, 0.17),
    (80.0, 0.53),       # 边界 80 有制度依据：对应"1 天 ±24%"，恰在创业板 20cm 涨停之外
    (150.0, 18.0),      # |u| > 80 处最小根对间距达 36，根对极稀疏
)


class TradeAnalyzer:
    """交易分析器 - 计算内部收益率(IRR)"""
    def __init__(self, records: List[Dict[str, Any]], current_price: float, symbol: str, end_ts: Optional[str] = None, window_days: int = 3):
        """
        records: 交易记录列表，格式:
            [
                {'ts': '2026-01-26 19:13:37', 'side': 'BUY', 'price': 1.92, 'delta_qty': 400},
                {'ts': '2026-01-26 19:15:00', 'side': 'SELL', 'price': 2.01, 'delta_qty': -400},
                ...
            ]
        current_price: 当前市价
        end_ts: 期末时间（格式 'YYYY-MM-DD HH:MM:SS'），默认为当前时间(run中)
        window_days: 做T判定窗口（天数），持仓天数 <= window_days 视为做T，默认3天
        """
        self.records = records
        self.current_price = current_price
        self.symbol = symbol
        self.end_ts:str|None = end_ts
        self.window_days = window_days

    def run(self, T_allow_cross=True):
        """
        执行分析 - 使用离线延迟匹配识别做T交易

        流程：
        1. 全部记录读入 t_window
        2. 离线延迟匹配
        3. 收尾栈匹配
        4. 剩余交易进入战略持仓
        5. 计算浮动盈亏、终值现金流

        args:
            T_allow_cross: 是否允许交叉匹配（如BSBS中2-3,1-4)，默认True
        """
        self.t_window = []  # 做T匹配窗口
        self.t_trades = []  # 做T交易
        self.strategic_inventory = []  # 战略持仓（LIFO栈）
        self.strategic_trades = []  # 战略已平仓
        self.cash_flows = []  # 全部现金流
        self.strategic_cash_flows = []  # 战略现金流

        # === 阶段1: 全部读入 t_window ===
        for r in self.records:
            ts = datetime.strptime(r['ts'], "%Y-%m-%d %H:%M:%S")
            price = float(r['price'])
            qty = abs(int(r['delta_qty']))
            side = 'BUY' if int(r['delta_qty']) > 0 else 'SELL'

            if r['side'] in ('RECORD', 'RESET'): # 防御过滤（与 preprocess_records 语义一致，防止绕过预处理的调用方受污染）
                continue

            self.t_window.append({
                'ts': ts,
                'price': price,
                'qty': qty,
                'side': side,
            })
        n = len(self.t_window)
        if n == 0:
            return
        # === 阶段2: 动态堆匹配 ===
        # 初始化双向链表索引（prev_idx / next_idx）
        import heapq

        for i in range(n):
            self.t_window[i]['prev_idx'] = i - 1 if i > 0 else None
            self.t_window[i]['next_idx'] = i + 1 if i < n - 1 else None
        
        heap = []
        # 找出所有初始相邻反向对，插入堆
        for i in range(n):
            a = self.t_window[i]
            if a['qty'] <= 0:
                continue
            nxt = a['next_idx']
            if nxt is None:
                continue
            b = self.t_window[nxt]
            if b['qty'] > 0 and a['side'] != b['side']:
                days = abs((a['ts'].date() - b['ts'].date()).days)
                if days <= self.window_days:
                    dist = abs((a['ts'] - b['ts']).total_seconds())
                    heapq.heappush(heap, (dist, i, nxt))

        # 用于不允许交叉模式下的已匹配时间对列表
        matched_ts_pairs = [] if not T_allow_cross else None

        while heap:
            dist, idx_a, idx_b = heapq.heappop(heap)
            a = self.t_window[idx_a]
            b = self.t_window[idx_b]

            # 惰性删除：必须是现有链表中的相邻反向对，且均有余额
            if a['next_idx'] != idx_b or b['prev_idx'] != idx_a:
                continue
            if a['qty'] <= 0 or b['qty'] <= 0:
                continue
            if a['side'] == b['side']:
                continue

            # 确定买卖时间（用于交叉检查和记录）
            if a['side'] == 'SELL':
                sell_ts, buy_ts = a['ts'], b['ts']
            else:
                buy_ts, sell_ts = a['ts'], b['ts']

            # 交叉检查（仅在禁止交叉时执行）
            if not T_allow_cross and matched_ts_pairs:
                # 已匹配对为 (mb, ms)，其中 mb=买入时间，ms=卖出时间
                if any((buy_ts < ms < mb < sell_ts) or (sell_ts < mb < ms < buy_ts)
                    for mb, ms in matched_ts_pairs):
                    continue

            # ---- 执行匹配 ----
            match_qty = min(a['qty'], b['qty'])
            if a['side'] == 'SELL':
                self._record_t_trade(buy_ts, b['price'], sell_ts, a['price'], match_qty)
            else:
                self._record_t_trade(buy_ts, a['price'], sell_ts, b['price'], match_qty)

            a['qty'] -= match_qty
            b['qty'] -= match_qty

            # 记录已匹配对（仅禁止交叉时）
            if not T_allow_cross and matched_ts_pairs:
                matched_ts_pairs.append((buy_ts, sell_ts))

            # ---- 删除耗尽节点，并检查新形成的相邻反向对 ----
            for idx in (idx_a, idx_b):
                node = self.t_window[idx]
                if node['qty'] > 0:
                    continue
                p = node['prev_idx']
                n = node['next_idx']
                # 从链表摘除
                if p is not None:
                    self.t_window[p]['next_idx'] = n
                if n is not None:
                    self.t_window[n]['prev_idx'] = p

                # 检查新相邻对 (p, n)
                if p is not None and n is not None:
                    p_node = self.t_window[p]
                    n_node = self.t_window[n]
                    if p_node['qty'] > 0 and n_node['qty'] > 0 and p_node['side'] != n_node['side']:
                        if abs((p_node['ts'].date() - n_node['ts'].date()).days) <= self.window_days:
                            nd = abs((p_node['ts'] - n_node['ts']).total_seconds())
                            heapq.heappush(heap, (nd, p, n))
                node['qty'] = 0   # 确保后续不会再被使用
        self.t_trades = sorted(self.t_trades, key=lambda x: x['sell_ts'])


        # === 阶段3: 剩余未匹配的进入战略持仓 ===
        for trade in self.t_window:
            if trade['qty'] > 0:
                self._add_to_strategic_inventory(trade)

        # 计算浮动盈亏
        self.unrealized_pnl = sum(
            self.current_price * i['qty']
            - self._calculate_trade_fee(self.current_price, i['qty'], 'SELL',i['ts'])
            - i['price'] * i['qty']
            - self._calculate_trade_fee(i['price'], i['qty'], 'BUY',i['ts'])
            for i in self.strategic_inventory
        )

        # 添加终值现金流：剩余 inventory 的 BUY + 虚拟 SELL
        if self.end_ts:
            end_datetime = datetime.strptime(self.end_ts, "%Y-%m-%d %H:%M:%S")
        else:
            end_datetime = datetime.now()
        for lot in self.strategic_inventory:
            buy_amount = lot['price'] * lot['qty']
            buy_fee = self._calculate_trade_fee(lot['price'], lot['qty'], 'BUY', lot['ts'])
            buy_cf = (lot['ts'], -(buy_amount + buy_fee))
            self.strategic_cash_flows.append(buy_cf)
            self.cash_flows.append(buy_cf)
        current_holding_val = sum(
            i['qty'] * self.current_price - self._calculate_trade_fee(self.current_price, i['qty'], 'SELL', end_datetime)
            for i in self.strategic_inventory
        )
        sell_cf = (end_datetime, current_holding_val)
        self.strategic_cash_flows.append(sell_cf)
        self.cash_flows.append(sell_cf)

        # 首次交易时间：从全部现金流中取最早的时间戳
        if self.cash_flows:
            self.first_trade_ts = min(cf[0] for cf in self.cash_flows)
        else:
            self.first_trade_ts = None

    def _record_t_trade(self, buy_ts: datetime, buy_price: float, sell_ts: datetime, sell_price: float, match_qty: int) -> None:
        """
        记录一笔做T交易
        Args:
            buy_ts: 买入时间
            buy_price: 买入价格
            sell_ts: 卖出时间
            sell_price: 卖出价格
            match_qty: 匹配数量
        Effects:
            - append self.t_trades
            - append self.cash_flows
        """
        days = abs((sell_ts - buy_ts).total_seconds()) / 86400.0
        days = max(days, 0.001)
        buy_amount = buy_price * match_qty
        sell_amount = sell_price * match_qty
        buy_fee = self._calculate_trade_fee(buy_price, match_qty, 'BUY', buy_ts)
        sell_fee = self._calculate_trade_fee(sell_price, match_qty, 'SELL', sell_ts)
        investment = buy_amount + buy_fee
        pnl = sell_amount - sell_fee - investment
        ret = pnl / investment
        arr = f"{ret * 365.0 / days * 100:+.1f}%"
        if len(arr) > 8 and arr[0] == '+':
            arr = "+>10000%"
        elif len(arr) > 8 and arr[0] == '-':
            arr = "-<10000%"

        t_type = ' 反T' if buy_ts > sell_ts else ' 正T'

        self.t_trades.append({
            'sell_ts': sell_ts.strftime("%Y-%m-%d %H:%M:%S"),
            'buy_ts': buy_ts.strftime("%Y-%m-%d %H:%M:%S"),
            'qty': match_qty,
            'buy_price': buy_price,
            'sell_price': sell_price,
            'days': days,
            'pnl': pnl,
            'investment': investment,
            'fee': buy_fee + sell_fee,
            'rar': f"{ret * 100:+.1f}%",
            'arr': arr,
            't_type': t_type,
        })

        # 做T现金流
        self.cash_flows.append((buy_ts, -buy_amount - buy_fee))
        self.cash_flows.append((sell_ts, sell_amount - sell_fee))

    def _add_to_strategic_inventory(self, trade):
        """超时交易加入战略持仓并进行匹配（LIFO）"""
        if trade['side'] == 'BUY':
            # 买入加入战略持仓
            self.strategic_inventory.append({
                'price': trade['price'],
                'qty': trade['qty'],
                'ts': trade['ts']
            })
        else:
            # 卖出：LIFO匹配战略持仓
            sell_qty = trade['qty']
            sell_price = trade['price']
            while sell_qty > 0 and self.strategic_inventory:
                last_lot = self.strategic_inventory[-1]
                exec_qty = min(sell_qty, last_lot['qty'])

                days = (trade['ts'] - last_lot['ts']).total_seconds() / 86400.0
                days = max(days, 0.001)
                buy_amount = last_lot['price'] * exec_qty
                sell_amount = sell_price * exec_qty
                buy_fee = self._calculate_trade_fee(last_lot['price'], exec_qty, 'BUY', last_lot['ts'])
                sell_fee = self._calculate_trade_fee(sell_price, exec_qty, 'SELL', trade['ts'])
                investment = buy_amount + buy_fee
                pnl = sell_amount - sell_fee - investment
                ret = pnl / investment
                arr = f"{ret * 365.0 / days * 100:+.1f}%"
                if len(arr) > 8 and arr[0]=='+':
                    arr = "+>10000%"
                elif len(arr) > 8 and arr[0]=='-':
                    arr = "-<10000%"

                self.strategic_trades.append({
                    'sell_ts': trade['ts'].strftime("%Y-%m-%d %H:%M:%S"),
                    'buy_ts': last_lot['ts'].strftime("%Y-%m-%d %H:%M:%S"),
                    'buy_price': last_lot['price'],
                    'sell_price': trade['price'],
                    'qty': exec_qty,
                    'theoretical_pnl': sell_amount - buy_amount,
                    'pnl': pnl,
                    'days': days,
                    'investment': investment,
                    'fee': buy_fee + sell_fee,
                    'rar': f"{ret*100:+.1f}%",
                    'arr': arr,
                })

                # 匹配时记录现金流：BUY 和 SELL 各按自己的 ts
                buy_cf = (last_lot['ts'], -buy_amount - buy_fee)
                sell_cf = (trade['ts'], sell_amount - sell_fee)
                self.strategic_cash_flows.append(buy_cf)
                self.strategic_cash_flows.append(sell_cf)
                self.cash_flows.append(buy_cf)
                self.cash_flows.append(sell_cf)

                last_lot['qty'] -= exec_qty
                sell_qty -= exec_qty
                if last_lot['qty'] < 0.001:
                    self.strategic_inventory.pop()

    def get_total_pnl(self):
        """获取总盈亏"""
        t_pnl = sum(t['pnl'] for t in self.t_trades)
        strategic_pnl = sum(t['pnl'] for t in self.strategic_trades)
        return t_pnl + strategic_pnl + self.unrealized_pnl

    def calculate_xirr(self, exclude_t=True):
        """
        求解 XIRR，返回全部实根 [(r, u), ...]（按 r 升序；空列表表示无有效解）

        r = e^u - 1 是年化收益率，u = ln(1+r) 是求解所用的自变量，两者一并返回。
        之所以不能只给 r：r 逼近 -100% 时表示精度急剧下降（u ≈ -36 时，相邻两个
        可表示的 r 对应的 u 相差约 0.5），凡需精细计算之处（如把年化折算回区间收益）
        都必须用 u —— 用 r 会得到 -100% 而真值可能是 -30%。

        先在自变量 u 上扫描定位 NPV 的变号区间，再在各区间内用阻尼牛顿法细化：
        扫描依据中值定理，保证不漏根；牛顿法保证区间内的收敛速度与精度。
        取 u = ln(1+r) 而非 r 本身，是因为 r 的定义域 (-1, +∞) 下界是开区间，
        迭代一旦越界 (1+r) 即变负，math.pow 便抛 domain error；u 域为全体实数，
        从根上消除了越界问题，同时顺带解决了 r 冲向无穷时的溢出。
        """
        cash_flows = self.strategic_cash_flows if exclude_t else self.cash_flows
        if not cash_flows:
            return []
        amounts = [cf[1] for cf in cash_flows]
        min_date = min(cf[0] for cf in cash_flows)
        years = [(cf[0] - min_date).total_seconds() / 31536000.0 for cf in cash_flows]

        roots = []
        for u_lo, u_hi in self._xirr_sign_change_intervals(years, amounts):
            u = self._xirr_newton(years, amounts, (u_lo + u_hi) / 2.0)
            if u is None:  # 牛顿法未收敛时退回二分，保证区间内一定有结果输出
                u = self._xirr_bisect(years, amounts, u_lo, u_hi)
            # 相邻区间可能收敛到同一个根，按 u 去重（u 域精度全程均匀）
            if not any(abs(u - x) < 1e-9 for x in roots):
                roots.append(u)
        return [(math.exp(u) - 1, u) for u in sorted(roots)]

    def _xirr_npv(self, u, years, amounts):
        """u 域上的净现值：NPV(u) = Σ a·e^(-y·u)"""
        return sum(a * math.exp(-y * u) for a, y in zip(amounts, years))

    def _xirr_npv_prime(self, u, years, amounts):
        """净现值对 u 的导数"""
        return sum(-y * a * math.exp(-y * u) for a, y in zip(amounts, years))

    def _xirr_u_range(self, years):
        """自变量的安全区间 (u_min, u_max)：下界防 e^(-y·u) 溢出，上界即扫描可达范围"""
        u_max = XIRR_SCAN_LIMIT
        # 600 系 math.exp 溢出阈值(约 709)留出余量后的安全指数，除以最长持有年数得到 |u| 上界
        return -min(u_max, 600.0 / max(max(years), 1e-9)), u_max

    def _xirr_sign_change_intervals(self, years, amounts):
        """扫描 u 域上 NPV 的正负号变化，返回疑似含根的 (u_lo, u_hi) 区间列表"""
        intervals = []
        prev_u, prev_v = None, None
        for u in self._xirr_scan_grid(years):
            v = self._xirr_npv(u, years, amounts)
            if prev_v is not None and (prev_v > 0) != (v > 0):
                intervals.append((prev_u, u))
            prev_u, prev_v = u, v
        return intervals

    def _xirr_scan_grid(self, years):
        """构造关于 0 对称的四层非均匀扫描网格：中间区步长细，外围逐级放大"""
        u_min, u_max = self._xirr_u_range(years)
        limit = min(abs(u_min), u_max)
        pos = [0.0]
        prev_edge = 0.0
        for edge, step in XIRR_SCAN_LAYERS:
            edge = min(edge, limit)
            if edge <= prev_edge:   # 已越过 limit 时，下一层必然在此退出
                break
            n = max(1, math.ceil((edge - prev_edge) / step))
            pos.extend(prev_edge + (edge - prev_edge) * i / n for i in range(1, n + 1))
            prev_edge = edge
        return [-u for u in reversed(pos[1:])] + pos

    def _xirr_newton(self, years, amounts, u0):
        """
        阻尼牛顿法：每步先试算，若 |NPV| 相对当前点未下降就把步长减半重试

        未收敛（找不到下降方向，或落点精度不达标）时返回 None，交由二分法兜底。
        """
        scale = sum(abs(a) for a in amounts)
        u_min, u_max = self._xirr_u_range(years)
        u = u0
        for _ in range(60):  # 牛顿法二次收敛，几十次迭代远够
            v = self._xirr_npv(u, years, amounts)
            dv = self._xirr_npv_prime(u, years, amounts)
            if dv == 0:
                return None
            step = v / dv
            alpha = 1.0
            while alpha > 1e-10:
                nu = u - alpha * step
                # 越出安全区间就减半，对应 r 域里的"越界减半"
                if not u_min <= nu <= u_max:
                    alpha *= 0.5
                    continue
                if abs(self._xirr_npv(nu, years, amounts)) < abs(v):
                    break
                alpha *= 0.5
            else:
                break
            du = abs(nu - u)
            u = nu
            if du < 1e-15 * max(1.0, abs(u)):
                break
        return u if abs(self._xirr_npv(u, years, amounts)) < 1e-9 * scale else None

    def _xirr_bisect(self, years, amounts, u_lo, u_hi):
        """二分法兜底：只依赖 NPV 的正负号，不需要导数，对病态形状也稳定"""
        v_lo = self._xirr_npv(u_lo, years, amounts)
        for _ in range(60):  # 初始区间宽 ≤0.08，60 次二分已远超 double 分辨率
            u_mid = (u_lo + u_hi) / 2.0
            v_mid = self._xirr_npv(u_mid, years, amounts)
            if v_lo * v_mid <= 0:
                u_hi = u_mid
            else:
                u_lo, v_lo = u_mid, v_mid
        return (u_lo + u_hi) / 2.0

    def get_t_metrics(self) -> Dict:
        """计算做T交易指标"""
        return self._calculate_metrics(self.t_trades)

    def get_strategic_metrics(self) -> Dict:
        """计算战略交易指标"""
        return self._calculate_metrics(self.strategic_trades)
    
    def _calculate_metrics(self, trades: List[Dict]) -> Dict:
        """通用指标计算"""
        if not trades:
            return {'count': 0, 'total_pnl': 0, 'avg_pnl': 0, 'win_rate': 0, 'profit_ratio': 0, 'expectancy': 0, 'contribution': 0}

        wins = [t for t in trades if t['pnl'] > 0]
        losses = [t for t in trades if t['pnl'] <= 0]

        total_pnl = sum(t['pnl'] for t in trades)
        avg_pnl = total_pnl / len(trades)
        win_count = len(wins)
        loss_count = len(losses)
        total_count = len(trades)

        win_rate = win_count / total_count if total_count > 0 else 0
        avg_win = sum(t['pnl'] for t in wins) / win_count if win_count > 0 else 0
        avg_loss = abs(sum(t['pnl'] for t in losses) / loss_count) if loss_count > 0 else 0
        profit_ratio = avg_win / avg_loss if avg_loss > 0 else float('inf')
        expectancy = win_rate * profit_ratio - (1 - win_rate)

        total_pnl_all = self.get_total_pnl()
        contribution = (total_pnl / total_pnl_all * 100) if total_pnl_all != 0 else 0

        return {
            'count': total_count,
            'total_pnl': total_pnl,
            'avg_pnl': avg_pnl,
            'win_rate': win_rate,
            'profit_ratio': profit_ratio,
            'expectancy': expectancy,
            'contribution': contribution
        }
    
    # 公共辅助函数区
    @staticmethod
    def is_fund(price: float, symbol: str) -> bool:
        return price < FUND_PRICE_THRESHOLD or 'ETF' in symbol.upper()

    @staticmethod
    def calculate_trade_fee(price: float, qty: int, side: str, symbol: str, ts: datetime|None = None, force_HT: bool = False) -> float:
        amount = price * qty
        use_old = ts is None or ts < FEE_CUTOVER_DATE or force_HT
        if TradeAnalyzer.is_fund(price, symbol):
            if use_old:
                return max(amount * FUND_COMMISSION_RATE, FUND_MIN_FEE)
            return max(amount * FUND_COMMISSION_RATE_DB, FUND_MIN_FEE_DB)

        if use_old:
            fee = max(amount * STOCK_COMMISSION_RATE, STOCK_MIN_COMMISSION)
        else:
            fee = max(amount * STOCK_COMMISSION_RATE_DB, STOCK_MIN_COMMISSION_DB)
        fee += round(amount * STOCK_TRANSFER_FEE, 2)
        if side == 'SELL':
            fee += round(amount * STOCK_STAMP_DUTY_RATE, 2)
        return fee

    # 实例转发：类内调用自动携带 self.symbol
    def _calculate_trade_fee(self, price: float, qty: int, side: str, ts: datetime|None = None, force_HT: bool = False) -> float:
        return self.calculate_trade_fee(price, qty, side, self.symbol, ts, force_HT)


def preprocess_records(
    records: List[Dict[str, Any]],
    start_ts: Optional[str] = None,
    end_ts: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    预处理交易记录
    1. 时间筛选
    2. 过滤非交易记录：RESET（其 delta_qty 为当前持仓量（大正数），会被 run 误当 BUY 参与配对）、RECORD（仅记录价格）
    """
    filtered = []
    for r in records:
        if r['side'] in ('RESET', 'RECORD'):
            continue
        if start_ts and r['ts'] < start_ts:
            continue
        if end_ts and r['ts'] > end_ts:
            continue
        filtered.append(r)
    return filtered


def _fmt_xirr_root(solution) -> str:
    """
    格式化单个 XIRR 根用于报告展示，入参为 calculate_xirr 返回的 (r, u)。

    r 按 %.2f%% 打印时，r ≤ -99.995% 会显示成 -100.00%；再往下相邻两个可表示的 r
    对应的 u 相差越来越大（u ≈ -36 处已达 0.5），单看百分比已无分辨力，
    此时附上求解所用的 u。这是显示层阈值，与 u_tolerance 描述的表示精度极限不同。
    """
    r, u = solution
    note = f" (u={u:.2f})" if r * 100 <= -99.99 else ""
    return f"{r * 100:+.2f}%{note}"


def analyze_and_print_irr(analyzer: TradeAnalyzer, exclude_t: bool = True) -> None:
    """
    分析并打印 IRR 分析报告（包含贡献度）

    贡献度计算：每笔交易盈亏 / 总盈亏的百分比
    """
    # ANSI 颜色代码
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    CYAN = '\033[96m'
    BLUE = '\033[94m'
    GREY = '\033[90m'
    RESET = '\033[0m'

    if analyzer.end_ts:
        end_ts = datetime.strptime(analyzer.end_ts, "%Y-%m-%d %H:%M:%S")
    else:
        end_ts = datetime.now()

    total_pnl = analyzer.get_total_pnl()
    xirr_solutions = analyzer.calculate_xirr(exclude_t=exclude_t)              # [(r, u), ...]
    xirr_roots = [r * 100 for r, _ in xirr_solutions]       # 展示用年化收益率
    t_pnl = sum(t['pnl'] for t in analyzer.t_trades)
    strategic_pnl = sum(t['pnl'] for t in analyzer.strategic_trades)
    t_fee = sum(t.get('fee', 0) for t in analyzer.t_trades)
    strategic_fee = sum(t.get('fee', 0) for t in analyzer.strategic_trades)
    holding_fee = sum(
        analyzer._calculate_trade_fee(lot['price'], lot['qty'], 'BUY', lot['ts'])
        + analyzer._calculate_trade_fee(analyzer.current_price, lot['qty'], 'SELL', end_ts)
        for lot in analyzer.strategic_inventory
    )
    total_fee = t_fee + strategic_fee + holding_fee

    # 资本效益指标累加（在循环中计算）
    total_investment = 0  # 累计流入资本
    total_outflow = 0     # 累计流出资本
    capital_days = 0      # 资本×天数累加

    print(f"\n{CYAN}{'=' * 115}{RESET}")
    print(f"{CYAN} 📊 【{analyzer.symbol}】多维度投资分析报告{RESET}")
    print(f"{CYAN}{'=' * 115}{RESET}")

    # 做 T 交易区块
    if analyzer.t_trades:
        print(f"\n{YELLOW}做T交易(买卖间隔≤{analyzer.window_days}天):{RESET}")
        print(f"{'卖出时间':<16} {'买入时间':<16} {'单笔投入':<8} {'经历天数':<6} {'实现盈亏':<6} {'手续费':<5} {'T类型':<6} {'ROI(单笔)':<8} {'ARR(年化)'}")
        print("-" * 115)

        for t in analyzer.t_trades:
            color = GREEN if t['pnl'] <= 0 else RED
            t_type = t.get('t_type', '错误')
            print(f"{t['sell_ts']:<20} {t['buy_ts']:<20} {t['investment']:<12.2f} "
                  f"{t['days']:<10.3f} "
                  f"{color}{t['pnl']:<+10.2f}{RESET} "
                  f"{t['fee']:<8.2f} "
                  f"{CYAN if t_type == ' 正T' else BLUE}{t_type:<7}{RESET} "
                  f" {color}{t['rar']:<9}{RESET} "
                  f"{color}{t['arr']}{RESET}"
                  )
            # 累加资本指标
            total_investment += t['investment']
            total_outflow += t['investment'] + t['pnl']
            sign = -1 if t.get('t_type') == ' 反T' else 1
            capital_days += t['investment'] * t['days'] * sign

        # 做T统计
        metrics = analyzer.get_t_metrics()
        total_pnl_str = f"{metrics['total_pnl']:+.2f}"
        avg_pnl_str = f"{metrics['avg_pnl']:+.2f}"
        contribution_str = f"{metrics['contribution']:+.1f}%"
        win_rate_str = f"{metrics['win_rate']*100:.1f}%"
        profit_ratio_str = f"{metrics['profit_ratio']:.2f}" if metrics['profit_ratio'] != float('inf') else "--"


        print(f"手续费共计: {GREEN}{t_fee:.2f}{RESET}")

        print(f"做T统计:")
        print(f"  总笔数: {metrics['count']:<10} 总利润: {GREEN if metrics['total_pnl'] <= 0 else RED}{total_pnl_str:<10}{RESET} 均利润: {GREEN if metrics['avg_pnl'] <= 0 else RED}{avg_pnl_str:<10}{RESET}")
        print(f"  贡献度: {GREY if metrics['contribution'] <= 0 else CYAN}{contribution_str:<10}{RESET} 总胜率: {win_rate_str:<10} 盈亏比: {profit_ratio_str:<10}")
        print(f"  期望倍: {metrics['expectancy']:.2f}/笔（相对平均亏损的期望收益，即承担单位亏损风险的收益，大于0表示盈利）")
        if metrics['profit_ratio']!=0:
            print(f"  下次投入: {metrics['expectancy']/metrics['profit_ratio']:.3f}×该品种计划资产(÷止损损失率)[根据凯利公式从期望倍、盈亏比计算，震荡趋势可计入括号]")
        else:
            print(f"  下次投入: --（盈亏比为0，从未实现盈利，无法计算）")
        print("-" * 115)

    # 已平仓交易
    if analyzer.strategic_trades:
        print(f"\n{YELLOW}已平仓交易:{RESET}")
        print(f"{'卖出时间':<16} {'买入时间':<16} {'单笔投入':<8} {'持仓天数':<6} {'实现盈亏':<6} {'手续费':<5} {'贡献度':<5} {'ROI(单笔)':<8} {'ARR(年化)'}")
        print("-" * 115)

        for t in analyzer.strategic_trades:
            color = GREEN if t['pnl'] <= 0 else RED
            # 贡献度：这笔交易盈亏占总盈亏的百分比
            contrib = (t['pnl'] / total_pnl * 100) if total_pnl != 0 else 0
            contrib_str = f"{contrib:+.1f}%"
            virtual_mark = ' *' if t.get('is_virtual', False) else ''
            print(f"{t['sell_ts'][0:19]:<20} {t['buy_ts']:<20} {t['investment']:<12.2f} "
                  f"{t['days']:<10.3f} "
                  f"{color}{t['pnl']:<+10.2f}{RESET}{virtual_mark} "
                  f"{t['fee']:<8.2f} "
                  f"{GREY if contrib<= 0 else CYAN}{contrib_str:<8}{RESET} "
                  f" {color}{t['rar']:<9}{RESET} "
                  f" {color}{t['arr']}{RESET}"
                  )
            # 累加资本指标
            total_investment += t['investment']
            total_outflow += t['investment'] + t['pnl']  # 卖出金额 = 投入 + 盈亏
            capital_days += t['investment'] * t['days']

        print(f"手续费共计: {GREEN}{strategic_fee:.2f}{RESET}")

        # 战略交易统计
        metrics = analyzer.get_strategic_metrics()
        total_pnl_str = f"{metrics['total_pnl']:+.2f}"
        avg_pnl_str = f"{metrics['avg_pnl']:+.2f}"
        contribution_str = f"{metrics['contribution']:+.1f}%"
        win_rate_str = f"{metrics['win_rate']*100:.1f}%"
        profit_ratio_str = f"{metrics['profit_ratio']:.2f}" if metrics['profit_ratio'] != float('inf') else "--"

        print(f"平仓统计:")
        print(f"  总笔数: {metrics['count']:<10} 总利润: {GREEN if metrics['total_pnl'] <= 0 else RED}{total_pnl_str:<10}{RESET} 均利润: {GREEN if metrics['avg_pnl'] <= 0 else RED}{avg_pnl_str:<10}{RESET}")
        print(f"  贡献度: {GREY if metrics['contribution'] <= 0 else CYAN}{contribution_str:<10}{RESET} 总胜率: {win_rate_str:<10} 盈亏比: {profit_ratio_str:<10}")
        print(f"  期望倍: {metrics['expectancy']:.2f}/笔（相对于平均亏损的期望收益倍数，即承担单位亏损风险的收益，大于0表示盈利）")
        if metrics['profit_ratio']!=0:
            print(f"  下次投入: {metrics['expectancy']/metrics['profit_ratio']:.3f}×该品种计划资产(÷止损损失率)[根据凯利公式从期望倍、盈亏比计算，震荡趋势可计入括号]")
        else:
            print(f"  下次投入: --（盈亏比为0，从未实现盈利，无法计算）")
        print("-" * 115)

    # 当前持仓
    rest_investment = 0
    if analyzer.strategic_inventory:
        print(f"\n{YELLOW}当前持仓:{RESET}")
        print(f"{'买入时间':<16} {'成本价格':<6} {'持仓余量':<5} {'剩余投入':<8} {'持仓天数':<6} {'浮动盈亏':<6} {'手续费':<5} {'贡献度':<5} {'[浮动]ROI':<8} {'[虚拟]ARR'}")
        print("-" * 115)

        for lot in analyzer.strategic_inventory:
            days = (end_ts - lot['ts']).total_seconds() / 86400.0
            days = max(days, 0.001)
            buy_amount = lot['price'] * lot['qty']
            sell_amount = analyzer.current_price * lot['qty']
            buy_fee = analyzer._calculate_trade_fee(lot['price'], lot['qty'], 'BUY', lot['ts'])
            sell_fee = analyzer._calculate_trade_fee(analyzer.current_price, lot['qty'], 'SELL', end_ts)
            investment = buy_amount + buy_fee
            fee = buy_fee + sell_fee
            pnl = sell_amount - sell_fee - investment
            ret = pnl / investment
            arr = ret * 365.0 / days * 100

            # 贡献度：这笔持仓盈亏占总盈亏的百分比
            contrib = (pnl / total_pnl * 100) if total_pnl != 0 else 0
            contrib_str = f"{contrib:+.1f}%"
            rest_investment += lot['qty']
            color = GREEN if pnl <= 0 else RED
            rar_str = f"{ret*100:+.1f}%"
            print(f"{lot['ts'].strftime('%Y-%m-%d %H:%M:%S'):<20} "
                  f"{lot['price']:<10.3f} {lot['qty']:<10}{investment:<12.2f} "
                  f"{days:<10.3f} "
                  f"{color}{pnl:<+10.2f}{RESET} "
                  f"{fee:<8.2f} "
                  f"{GREY if contrib<= 0 else CYAN}{contrib_str:<8}{RESET} "
                  f" {color}{rar_str:<9}{RESET} "
                  f" {color}{arr:+.1f}%{RESET}"
                  )
            # 累加资本指标
            total_investment += investment
            capital_days += investment * days
        print(f"手续费预估: {GREEN}{holding_fee:.2f}{RESET}")
        print("-" * 115)

    # 总结
    print(f"\n{YELLOW}总结:{RESET}")
    print(f"  当前价格: {analyzer.current_price:.3f}")
    print(f"  当前总额：{rest_investment*analyzer.current_price:.2f}")
    # print(f"  手续费合计: {total_fee:.2f} (T: {t_fee:.2f}, 已实现: {strategic_fee:.2f}, 持仓预估: {holding_fee:.2f})")

    # 资本效益指标（插入到当前总额和理论盈亏之间）
    avg_capital_return = 0
    if analyzer.first_trade_ts:
        total_days = max((end_ts - analyzer.first_trade_ts).total_seconds() / 86400.0, 1)
        avg_capital = capital_days / total_days
        avg_capital_return = (total_pnl / avg_capital * 100) if avg_capital != 0 else 0

        print(f"  累计流入资本: {total_investment:.2f}")
        print(f"  累计流出资本: {total_outflow:.2f}")
        print(f"  当前持仓成本：{rest_investment*analyzer.current_price-analyzer.unrealized_pnl:.2f}")
        print(f"  平均占用资本: {avg_capital:.2f} (时间加权, 共{total_days:.1f}天，未考虑初始持仓)")
    else:
        print(f"  {GREY}无匹配交易，无法计算资本效益指标{RESET}")

    print(f"  理论盈亏（计入手续费）: {GREEN if total_pnl <= 0 else RED}{total_pnl:+.2f}{RESET}")
    print(f"    ├─ T 收益: {GREEN if t_pnl <= 0 else RED}{t_pnl:+.2f}{RESET}")
    print(f"    ├─ 已实现: {GREEN if strategic_pnl <= 0 else RED}{strategic_pnl:+.2f}{RESET}")
    print(f"    └─ 未实现: {GREEN if analyzer.unrealized_pnl <= 0 else RED}{analyzer.unrealized_pnl:+.2f}{RESET}")
    print(f"       手续费: {GREEN if -total_fee <= 0 else RED}{-total_fee:+.2f}{RESET}")
    print(f"  [平均资本]回报率: {GREEN if avg_capital_return <= 0 else RED}{avg_capital_return:+.2f}%{RESET}")
    if xirr_roots:
        print(f"  {CYAN}总体 XIRR (年化): {_fmt_xirr_root(xirr_solutions[0])}{RESET} (不计入做T)")
        if len(xirr_roots) > 1:
            # 现金流多次变号会使 XIRR 方程出现多解，全部列出以避免只看到首解而误判
            others = "、".join(_fmt_xirr_root(s) for s in xirr_solutions[1:])
            print(f"  {YELLOW}  ⚠ 现金流多次变号，方程另有 {len(xirr_roots) - 1} 个解: {others}{RESET} (IRR 非唯一，仅供参考)")
    else:
        print(f"  {YELLOW}总体 XIRR (年化): 无有效解{RESET} (不计入做T)")
    # 计算从首次交易到现在的年化收益率
    if not xirr_roots:
        if analyzer.first_trade_ts:
            days_since_first = (end_ts - analyzer.first_trade_ts).total_seconds() / 86400.0
            first_trade_date = analyzer.first_trade_ts.strftime('%Y-%m-%d')
            # 从 records 中找首次交易对应的价格
            first_ts_str = analyzer.first_trade_ts.strftime("%Y-%m-%d %H:%M:%S")
            first_price = next((float(r['price']) for r in analyzer.records if r['ts'] == first_ts_str), None)
            if first_price:
                base_yield = (analyzer.current_price - first_price) / first_price * 100
                print(f"  {GREY}无有效解，无法折算区间收益 @ {first_trade_date}(基准：{base_yield:+.2f}%)){RESET}")
            else:
                print(f"  {GREY}无有效解，无法折算区间收益 @ {first_trade_date}{RESET}")
    elif analyzer.first_trade_ts:
        days_since_first = (end_ts - analyzer.first_trade_ts).total_seconds() / 86400.0
        # 折算必须走 u：r 逼近 -100% 时表示精度不足，直接代入会得到 -100% 的错误结果
        cumulative_return = (math.exp(xirr_solutions[0][1] * days_since_first / 365.0) - 1) * 100
        first_trade_date = analyzer.first_trade_ts.strftime('%Y-%m-%d')
        # 从 records 中找首次交易对应的价格
        first_ts_str = analyzer.first_trade_ts.strftime("%Y-%m-%d %H:%M:%S")
        first_price = next((float(r['price']) for r in analyzer.records if r['ts'] == first_ts_str), None)
        if first_price:
            base_yield = (analyzer.current_price - first_price) / first_price * 100
            print(f"  {CYAN}折算至首次交易起: {cumulative_return:+.2f}%{RESET} @ {first_trade_date}(基准：{base_yield:+.2f}%)")
        else:
            print(f"  {CYAN}折算至首次交易起: {cumulative_return:+.2f}%{RESET} @ {first_trade_date}")
    else:
        print(f"  {GREY}无匹配交易，无法计算区间收益{RESET}")
    print("")
    print(f"{CYAN}{'=' * 115}{RESET}\n")

