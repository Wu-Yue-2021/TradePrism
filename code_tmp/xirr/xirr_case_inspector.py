"""
XIRR 案例检查器

用途：排查具体案例为什么会解出某个根，以及 u 与实际收益率的换算。

功能：
  1. u -> 收益率换算表（同一 u 在不同持仓天数下差别极大，看表比看 u 直观）
  2. 列出样本中的极端多根案例（|u| 超过阈值）
  3. 给定 case_id，复现其现金流，分析指定 u 处是哪些交易在角力

用法：
  python code_tmp/xirr/xirr_case_inspector.py                     # 换算表 + 极端案例清单
  python code_tmp/xirr/xirr_case_inspector.py 2259 -149.07        # 深挖指定案例

依赖 xirr_root_sampler.py 的现金流生成器以复现同一批样本。
"""
import csv
import math
import os
import random
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xirr_root_sampler import SEED, gen_case  # noqa: E402

CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'xirr_root_sample.csv')
EXTREME_THRESHOLD = 100.0   # |u| 超过该值视为极端案例
DAYS = [1, 3, 7, 30]        # 换算表里展示的持仓天数


def u_to_return(u, days):
    """把自变量 u 换算成"持仓 days 天"的收益率"""
    return math.exp(u * days / 365.0) - 1


def print_return_table():
    """打印若干代表性 u 对应的收益率"""
    print('=' * 76)
    print('u -> 收益率换算（u = ln(1+r) × 365/持仓天数）')
    print('=' * 76)
    header = f'  {"u":>8} {"年化 r":>26}' + ''.join(f'{f"{d}天":>12}' for d in DAYS)
    print(header)
    for u in [-135.60, -52.06, -15.91, -10, -6.0, -3, 1.03, 3, 6.0, 20.0, 43.25, 89.22, 129.10]:
        r = math.exp(u) - 1 if u < 700 else float('inf')
        rs = f'{r * 100:>25.6f}%' if abs(r) < 1e6 else f'{r:>25.3e}'
        cells = ''
        for d in DAYS:
            x = u_to_return(u, d)
            cells += f'{x * 100:>11.1f}%' if abs(x) < 1e6 else f'{x:>11.1e}'
        print(f'  {u:>8.2f} {rs} {cells}')


def load_cases():
    cases = defaultdict(list)
    with open(CSV_PATH, encoding='utf-8') as f:
        for row in csv.DictReader(f):
            cases[int(row['case_id'])].append(float(row['u']))
    return cases


def list_extreme(cases, threshold=EXTREME_THRESHOLD):
    """列出 |u| 超过阈值的多根案例"""
    extreme = [(cid, sorted(us)) for cid, us in cases.items()
               if len(us) >= 2 and max(abs(u) for u in us) > threshold]
    extreme.sort(key=lambda x: -max(abs(u) for u in x[1]))
    print(f'\n{"=" * 76}')
    print(f'极端多根案例（|u| > {threshold:.0f}）：共 {len(extreme)} 组')
    print('=' * 76)
    for cid, us in extreme[:15]:
        gaps = [us[i + 1] - us[i] for i in range(len(us) - 1)]
        print(f'  case {cid:>6}: u = {[f"{u:+.2f}" for u in us]}  '
              f'最小间距 {min(gaps):.3f}')
    return extreme


def replay_case(case_id):
    """按相同种子复现指定案例的现金流"""
    rng = random.Random(SEED)
    for cid in range(case_id + 1):
        ys, amts = gen_case(rng)
    return ys, amts


def inspect(case_id, u_star):
    """分析 u_star 处是哪些交易在主导 NPV"""
    ys, amts = replay_case(case_id)
    # 影响力取对数：ln|a| - y·u，避免大 u 时溢出
    log_infl = np.log(np.abs(amts)) - ys * u_star
    order = np.argsort(-log_infl)
    top = log_infl[order[0]]

    print(f'\n{"=" * 76}')
    print(f'case {case_id} 在 u = {u_star:+.4f} 处的主导交易')
    print('=' * 76)
    print(f'  笔数 {len(ys)}, 跨度 {ys[-1] * 365:.1f} 天, '
          f'最小间隔 {np.diff(ys).min() * 365 * 24:.1f} 小时')
    print(f'  {"时间(天)":>10} {"金额":>10} {"相对影响力":>13}')
    for i in order[:5]:
        print(f'  {ys[i] * 365:>10.1f} {amts[i]:>+10.0f} {math.exp(log_infl[i] - top):>13.3e}')

    i, j = order[0], order[1]
    dt = abs(ys[i] - ys[j])
    print(f'\n  主导两笔: {amts[i]:+.0f} @ {ys[i] * 365:.1f}天  vs  '
          f'{amts[j]:+.0f} @ {ys[j] * 365:.1f}天')
    print(f'    时间间隔 {dt * 365:.2f} 天, 金额比 |a1/a2| = {abs(amts[i] / amts[j]):.4f}')
    print(f'    平衡条件 |a1/a2| = e^(Δt·u) = {math.exp(dt * abs(u_star)):.4f}  '
          f'（实际 {abs(amts[i] / amts[j]):.4f}，越接近说明两笔越势均力敌）')


def main():
    print_return_table()
    cases = load_cases()

    if len(sys.argv) >= 3:
        inspect(int(sys.argv[1]), float(sys.argv[2]))
    else:
        list_extreme(cases)
        print('\n  深挖某个案例: python code_tmp/xirr/xirr_case_inspector.py <case_id> <u>')


if __name__ == '__main__':
    main()
