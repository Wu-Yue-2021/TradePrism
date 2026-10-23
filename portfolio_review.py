"""组合复盘报告 —— 渲染层

只做格式化与分组，不含任何计算、不碰 I/O：所有数字由 irr_analyzer.PortfolioAnalyzer
算好放在结果字典里。

宽度工具自写而不 import trading_record_manager.Display，原因有二：
  1. 需要先剔除 ANSI 转义序列才能正确计宽（现成实现在着色字符串上会算宽偏大）
  2. 避开本模块与 trading_record_manager 的循环依赖

不使用 emoji：宽度类参差（部分属 N 只占 1 列）且终端渲染风格不一，全部改用纯文本标签。
"""
import re
import unicodedata
from typing import Any, Dict, List, Optional

# ---------- 版式常量 ----------
WIDTH = 110
LABEL_W = 22

# ---------- 颜色 ----------
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
BLUE = '\033[94m'
GREY = '\033[90m'
RESET = '\033[0m'

WEIGHT_2 = ('W', 'F', 'A')


# ===============================
# 显示宽度工具
# ===============================

def _strip_ansi(s: str) -> str:
    return re.sub(r'\x1b\[[0-9;]*m', '', str(s))


def visible_width(s: str) -> int:
    """终端显示宽度：剔除 ANSI 转义后按东亚宽度计（全宽/半宽/歧义均按现有工具约定计 2）"""
    return sum(2 if unicodedata.east_asian_width(c) in WEIGHT_2 else 1 for c in _strip_ansi(s))


def pad(s: Any, width: int, align: str = 'left') -> str:
    """按显示宽度补空格；可传入已着色的字符串，宽度计算会自动剔除转义序列"""
    s = str(s)
    padding = max(0, width - visible_width(s))
    if align == 'right':
        return ' ' * padding + s
    return s + ' ' * padding


# ===============================
# 格式化辅助
# ===============================

def _pnl_color(v: float) -> str:
    """涨跌配色沿用 A 股习惯：正红负绿；零值不着色（灰在浅色终端上近乎不可见）"""
    return RED if v > 0 else (GREEN if v < 0 else '')


def _money(v: float, sign: bool = False) -> str:
    return f'{v:+,.2f}' if sign else f'{v:,.2f}'


def _colored_money(v: float, sign: bool = True, width: Optional[int] = None) -> str:
    """金额着色；给 width 时按显示宽度**右对齐**补齐（与 f-string 的 :>N 同向）"""
    txt = _money(v, sign)
    if width:
        txt = pad(txt, width, 'right')
    color = _pnl_color(v)
    return f'{color}{txt}{RESET}' if color else txt


def _colored_pct(v: float, width: Optional[int] = None) -> str:
    txt = f'{v:+.2f}%'
    if width:
        txt = pad(txt, width, 'right')
    color = _pnl_color(v)
    return f'{color}{txt}{RESET}' if color else txt


def _pnl_breakdown(realized: float, unrealized: float) -> str:
    """「已实现 X 浮动 Y」片段：标签青色、数值按涨跌着色（不用灰，浅色终端上看不清）"""
    return (f'{CYAN}已实现{RESET} {_colored_money(realized, sign=True)}  '
            f'{CYAN}浮动{RESET} {_colored_money(unrealized, sign=True)}')


def _fmt_xirr(roots: List, empty: str = '—') -> str:
    """格式化 XIRR 解列表（可能多解或无解）"""
    if not roots:
        return f'{GREY}{empty}{RESET}'
    r, u = roots[0]
    txt = f'{r * 100:+.2f}%'
    if r * 100 <= -99.99:
        # r 逼近 -100% 时百分比失去分辨力，附上求解所用的 u
        txt += f' (u={u:.2f})'
    if len(roots) > 1:
        txt += f' {YELLOW}[多解 x{len(roots)}]{RESET}'
    return txt


def _rule(char: str = '─') -> str:
    return char * WIDTH


def _trade_line(t: Dict[str, Any], tag: str) -> str:
    """单笔交易的一行摘要（盈/亏 Top-N 用）"""
    return (f'      {tag} {_colored_money(t["pnl"], width=10)}  {pad(t["symbol"], 12)}'
            f'{t["sell_ts"][:16]}  {GREY}投入 {_money(t["investment"])} / 持 {t["days"]:.2f} 天{RESET}')


# ===============================
# 主入口
# ===============================

def print_portfolio_review(result: Dict[str, Any]) -> None:
    """打印完整复盘报告（本模块唯一对外入口，入参为 PortfolioAnalyzer.run() 的结果）"""
    _print_header(result)
    _print_section_1(result)
    _print_section_2(result)
    _print_section_3(result)
    _print_section_4(result)
    _print_footer()


def _print_header(result: Dict[str, Any]) -> None:
    p = result['period']
    print()
    print(f'{CYAN}{_rule("═")}{RESET}')
    print(f'{CYAN}交易复盘报告  |  {p["start"][:10]} ~ {p["end"][:10]}  |  '
          f'{p["symbol_count"]} 个标的 / {p["days"]:.1f} 天 / 做T窗口 {p["window_days"]} 天{RESET}')
    print(f'{CYAN}{_rule("═")}{RESET}')


def _print_section_1(result: Dict[str, Any]) -> None:
    a, p, r = result['assets'], result['pnl'], result['returns']
    ext, gap = result['external'], result['pnl']['reconcile_gap']
    print()
    print(f'{YELLOW}[1. 全局概况]{RESET}')

    print(f'  {pad("期初总资产", LABEL_W)}{_money(a["open_total"]):>14}  '
          f'= {CYAN}持仓{RESET} {_money(a["open_sec"])} + {CYAN}现金{RESET} {_money(a["open_cash"])}')
    print(f'  {pad("期末总资产", LABEL_W)}{_money(a["end_total"]):>14}  '
          f'= {CYAN}持仓{RESET} {_money(a["end_sec"])} + {CYAN}现金{RESET} {_money(a["end_cash"])}')
    print(f'  {pad("区间总净利", LABEL_W)}{_colored_money(p["total"], width=14)}  '
          f'= {CYAN}已实现{RESET} {_colored_money(p["realized"])} '
          f'+ {CYAN}浮动{RESET} {_colored_money(p["unrealized"])}')
    print(f'  {pad("综合收益率", LABEL_W)}{_colored_pct(r["on_open_assets"], width=14)}'
          f'  (按期初总资产 {_money(a["open_total"])})')
    print(f'  {pad("平均资本回报率", LABEL_W)}{_colored_pct(r["on_twac"], width=14)}'
          f'  (按组合 TWAC {_money(r["twac"])})')

    m = result['metrics']['all']
    pr = '--' if m['profit_ratio'] == float('inf') else f'{m["profit_ratio"]:.2f}'
    exp_txt = '--' if m['expectancy'] == float('inf') else f'{m["expectancy"]:+.3f}'
    print(f'  {pad("综合胜率", LABEL_W)}{m["win_rate"] * 100:>13.1f}%'
          f'  ({m["count"]} 笔已平仓 | 盈亏比 {pr} | 期望倍 {exp_txt})')
    print(f'  {pad("手续费", LABEL_W)}{_money(result["fees"]["paid"]):>14}'
          f'  {GREY}(已发生，期末持仓预估卖出费另计 {_money(result["fees"]["holding_est"])}){RESET}')
    print(f'  {pad("闭环 XIRR", LABEL_W)}{pad(_fmt_xirr(result["xirr"]["closed_s"]), 14, "right")}'
          f'  {GREY}(仅已平仓战略波段，年化){RESET}')
    print(f'  {pad("外部资金净流入", LABEL_W)}{_money(ext["external"], sign=True):>14}'
          f'  {GREY}(区间内无对应证券交易的现金净流入){RESET}')
    print(f'  {pad("对账差额", LABEL_W)}{_money(gap, sign=True):>14}'
          f'  {GREY}= 外部资金 {_money(ext["external"], sign=True)} '
          f'+ 费率舍入 {_money(gap - ext["external"], sign=True)}{RESET}')

    folds = result['folds']
    n_no = folds['no_interval_record']
    if n_no:
        print(f'  {YELLOW}! {len(n_no)} 个标的区间内无记录，合计市值 '
              f'{_money(folds["no_interval_record_mv"])}，其区间浮动按 0 计'
              f'（数据缺失，非"无波动"）{RESET}')


def _print_section_2(result: Dict[str, Any]) -> None:
    print()
    print(f'{YELLOW}[2. 策略拆解]{RESET}')

    for kind, label in (('s', '战略波段'), ('t', '做 T 交易')):
        m = result['metrics'][kind]
        ex = result['extra'][kind]
        print(f'  {CYAN}➤ {label}{RESET}  {m["count"]} 笔已平仓   '
              f'盈亏 {_colored_money(m["total_pnl"])}')
        if not m['count']:
            print(f'      {GREY}本期无此类交易{RESET}')
            continue
        pr = '--' if m['profit_ratio'] == float('inf') else f'{m["profit_ratio"]:.2f}'
        exp_txt = '--' if m['expectancy'] == float('inf') else f'{m["expectancy"]:+.3f}'
        print(f'      胜率 {m["win_rate"] * 100:.1f}%   盈亏比 {pr}   '
              f'期望倍 {exp_txt}   单笔均投入 {_money(ex["avg_investment"])}')
        print(f'      持仓天数：盈利单均 {ex["win_days"]:.2f} 天  vs  亏损单 {ex["loss_days"]:.2f} 天')
        for t in ex['best_list']:
            print(_trade_line(t, f'{RED}盈利{RESET}'))
        for t in ex['worst_list']:
            print(_trade_line(t, f'{GREEN}亏损{RESET}'))

    e = result['exposure']
    end_total = result['assets']['end_total']
    pct = e['total_end_mv'] / end_total * 100 if end_total else 0.0
    print(f'  {CYAN}➤ 未平仓敞口{RESET}')
    print(f'      期末持仓市值 {_money(e["total_end_mv"])}   '
          f'{GREY}(占期末总资产 {pct:.1f}%){RESET}')
    for key, label in (('legacy', '历史遗留底仓'), ('new', '本期新建持仓')):
        g = e[key]
        if not g['lots']:
            continue
        print(f'      {label}{g["lots"]:>3} lot   '
              f'市值 {_money(g["end_mv"]):>12}   区间浮动 {_colored_money(g["pnl"], width=11)}')
    print(f'      {GREY}（遗留底仓基准为期初价，本期新建基准为含费买入成本）{RESET}')


def _print_section_3(result: Dict[str, Any]) -> None:
    print()
    print(f'{YELLOW}[3. 标的透视]{RESET}')

    for label, key in (('盈利榜', 'top'), ('亏损榜', 'bottom')):
        items = result[key]
        if not items:
            continue
        print(f'  {CYAN}{label} Top {len(items)}{RESET}')
        for i, it in enumerate(items, 1):
            am = it['all_metrics']
            ex = it['exposure']
            print(f'    {i}. {pad(it["symbol"], 12)}{_colored_money(it["total_pnl"], width=12)}'
                  f'  {_pnl_breakdown(it["realized"], it["unrealized"])}')
            xirr_txt = f'XIRR {_fmt_xirr(it["xirr_full"])}'
            if am['count']:
                # 无亏损单时盈亏比为 inf（无盈利单时为 0），显示 -- 更直观
                pr = '--' if am['profit_ratio'] == float('inf') else f'{am["profit_ratio"]:.2f}'
                exp_txt = '--' if am['expectancy'] == float('inf') else f'{am["expectancy"]:+.3f}'
                print(f'       交易 {am["count"]} 笔（做T {it["t_metrics"]["count"]} / '
                      f'战略 {it["s_metrics"]["count"]}）| 胜率 {am["win_rate"] * 100:.1f}% | '
                      f'盈亏比 {pr} | 期望倍 {exp_txt}')
                print(f'       单笔均投入 {_money(it["all_extra"]["avg_investment"])} | {xirr_txt}')
            else:
                print(f'       {GREY}区间内无已平仓交易{RESET} | {xirr_txt}')
            print(f'       平均占用 TWAC    {_money(it["twac"])} | '
                  f'平均资本回报率 {_colored_pct(it["capital_return"])}')
            if ex['legacy']['lots'] or ex['new']['lots']:
                # 一组一行：基数（期初市值 / 含费买入成本）与区间浮动并排，免去读者配对；
                # 组名沿用 [2] 敞口节的叫法
                for group, label in (('legacy', '历史遗留底仓'), ('new', '本期新建持仓')):
                    g = ex[group]
                    if not g['lots']:
                        continue
                    print(f'       {pad(label, 14)}{_money(g["base"]):>11}   '
                          f'区间浮动 {_colored_money(g["pnl"], width=11)}')
            else:
                print(f'       {CYAN}期末已清仓{RESET}')
            # 代表单笔：该品种全部已平仓交易中盈亏最大/最小的那一笔（不分做T/战略），
            # 盈利榜取最大、亏损榜取最小；成交类型只作标注
            want_best = (key == 'top')
            lst = it['all_extra']['best_list' if want_best else 'worst_list']
            if lst:
                b = lst[0]
                kind = '战略' if b['kind'] == 'S' else '做T'
                print(f'       {GREY}{kind}{"最佳" if want_best else "最差"} {b["sell_ts"][:16]} '
                      f'投入 {_money(b["investment"])} 盈亏 {_money(b["pnl"], sign=True)}{RESET}')
        print("")
    others = result['folds']['others']
    if others:
        print(f'  {CYAN}其余 {len(others)} 个{RESET} {GREY}(未进榜单，按区间盈亏降序){RESET}')
        for s in others:
            print(f'    {pad(s["symbol"], 12)}{_colored_money(s["total_pnl"], width=12)}'
                  f'  {_pnl_breakdown(s["realized"], s["unrealized"])}')


def _print_section_4(result: Dict[str, Any]) -> None:
    r, c = result['risk'], result['cash']
    print()
    print(f'{YELLOW}[4. 行为风控]{RESET}')

    st = r['max_loss_streak']
    if st:
        print(f'  {pad("最大连亏", LABEL_W)}{pad(str(st["count"]) + " 笔", 14, "right")}'
              f'  {GREY}({st["from"]} ~ {st["to"]}，按平仓时间连续亏损笔数，'
              f'累计 {_money(st["sum"], sign=True)}){RESET}')
    else:
        print(f'  {pad("最大连亏", LABEL_W)}{pad("无", 14, "right")}')

    # 回撤是"损失量"，数值恒为正但语义是亏损，按 A 股习惯用跌色
    dd = r['nav_drawdown']
    span = f"{dd['from'][5:]} ~ {dd['to'][5:]}" if dd['from'] else '—'
    print(f'  {pad("净值最大回撤", LABEL_W)}'
          f'{GREEN}{pad(_money(dd["value"]), 14, "right")}{RESET}'
          f'  {GREY}({span}，逐日「持仓市值 + 现金」的净值曲线，已剔除出入金){RESET}')

    wt = r['worst_trade']
    if wt:
        print(f'  {pad("单笔最大亏损", LABEL_W)}{_colored_money(wt["pnl"], width=14)}'
              f'  {GREY}({wt["symbol"]} {wt["sell_ts"][:10]}){RESET}')

    print(f'  {pad("账户现金", LABEL_W)}{_colored_money(c["end"], width=14)}'
          f'  备付金充足率 {c["cover_ratio"]:+.1f}%')


def _print_footer() -> None:
    print()
    print(f'{GREY}{_rule()}')
    print('口径说明：')
    print('  · 区间盈亏按「平仓腿落在区间内」归因；期初持仓以期初价重定基，其占用基数亦为期初市值')
    print('  · 期末持仓按可卖出净值计（已扣预估卖出费），故"净利 = 资产变动 − 外部资金"自洽')
    print('  · [3] 的 XIRR 与单品种 IRR 界面同口径（含未平仓终值）；[1] 的闭环 XIRR 仅含已平仓战略波段')
    print('  · 做 T 交易用单笔年化（ARR）而非 XIRR：1~2 天的收益年化后是伪像')
    print('  · 手续费按工具费率公式估算，与券商实收存在小额差异')
    print(f'{_rule()}{RESET}')
