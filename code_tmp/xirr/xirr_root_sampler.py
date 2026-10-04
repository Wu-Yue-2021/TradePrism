"""
XIRR 多根分布采样器

用途：为 XIRR 非均匀网格的区间划分提供实测依据。

做法：生成海量模拟波段交易现金流（首笔建仓为负、中间反复进出、末笔终值为正），
      用极细网格求出每组的全部实根，换算成自变量 u = ln(1+r) 后写入 CSV。

输出：code_tmp/xirr/xirr_root_sample.csv  列: case_id, n_roots, u

该 CSV 是 xirr_grid_optimizer.py 的输入，本脚本不做任何区间划分判断。
"""
import csv
import os
import random
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import time

start_time = time.time()

OUT_PATH = 'code_tmp/xirr/xirr_root_sample.csv'
N_CASE = 50000       # 模拟案例数

# 采样范围：必须覆盖所有可能出现的根。
# 按 365 口径（现行代码的年化基数）折算 A 股各涨跌幅制度的极值：
#   主板 10%      -> u ∈ [-38.46, +34.79]
#   创业板 20cm   -> u ∈ [-81.45, +66.55]
#   核准制新股首日 +44% -> u = +133.1
# 故取 ±150 留出余量；注册制新股无涨跌幅限制的情形理论无界，不在采样范围内。
U_LIMIT = 150.0
N_GRID = 100000     # 极细网格点数，步长 = 2*150/100000 = 0.003
SEED = 20261005


def gen_case(rng):
    """模拟一段波段交易历史：随机笔数、随机跨度、多次进出"""
    n = rng.randint(6, 40)
    span = rng.uniform(0.2, 5.0)
    ts = np.array(sorted(rng.uniform(0.0, span) for _ in range(n)))
    amts = [-rng.uniform(1000, 5000)]           # 首笔锚点：建仓（负）
    direction = 1
    for _ in range(n - 2):
        if rng.random() < 0.35:                 # 切换方向，制造符号变号
            direction *= -1
        amts.append(direction * rng.uniform(500, 5000))
    amts.append(rng.uniform(500, 20000))        # 末笔锚点：终值（正）
    return ts, np.array(amts)


def npv_one(u, ys, amounts):
    """单点 NPV。提取主导指数作公因子，指数参数恒 ≤ 0，u 再负也不溢出；
    公因子恒为正，故符号与零点与原始 NPV 完全一致。"""
    y_ref = ys.max() if u < 0 else ys.min()
    return float(np.exp(-(ys - y_ref) * u) @ amounts)


def find_roots(ys, amounts, u_limit=U_LIMIT, n_grid=N_GRID):
    """极细网格扫描 + 二分细化，返回全部实根的 u 值（升序）"""
    us_grid = np.linspace(-u_limit, u_limit, n_grid)
    # 向量化求值，按 u 的符号分段归一化
    vals = np.empty_like(us_grid)
    neg = us_grid < 0
    vals[neg] = np.exp(-np.outer(us_grid[neg], ys - ys.max())) @ amounts
    vals[~neg] = np.exp(-np.outer(us_grid[~neg], ys - ys.min())) @ amounts
    hits = np.where(np.diff(np.sign(vals)) != 0)[0]

    roots = []
    for i in hits:
        lo, hi, flo = us_grid[i], us_grid[i + 1], vals[i]
        for _ in range(80):
            mid = (lo + hi) / 2
            fmid = npv_one(mid, ys, amounts)
            if flo * fmid <= 0:
                hi = mid
            else:
                lo, flo = mid, fmid
        u = (lo + hi) / 2
        # 相邻网格点可能夹住同一个根，去重
        if not roots or abs(u - roots[-1]) > 1e-9:
            roots.append(u)
    return roots


def _process_case(args):
    """Worker: 处理单个案例，返回 (case_id, n_roots, roots)"""
    case_id, ys, amounts = args
    roots = find_roots(ys, amounts)
    return case_id, len(roots), roots


def main():
    rng = random.Random(SEED)
    # 预生成所有案例（随机数生成很快，瓶颈在 find_roots）
    cases = [(i, *gen_case(rng)) for i in range(N_CASE)]

    rows = []
    n_multi = 0
    n_workers = os.cpu_count() or 4

    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        done = 0
        for case_id, n_roots, roots in executor.map(_process_case, cases, chunksize=100):
            if n_roots >= 2:
                n_multi += 1
            for u in roots:
                rows.append((case_id, n_roots, f'{u:.10f}'))
            done += 1
            if done % 1000 == 0:
                print(f"done:{done},time_cost:{time.time() - start_time:.2f}s")
    if not os.path.exists(OUT_PATH):
        os.makedirs(os.path.dirname(OUT_PATH))
    with open(OUT_PATH, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['case_id', 'n_roots', 'u'])
        writer.writerows(rows)

    print(f'已写出 {OUT_PATH}')
    print(f'  案例 {N_CASE} 组, 根 {len(rows)} 个')
    print(f'  多根案例 {n_multi} 组 ({n_multi / N_CASE * 100:.1f}%)')


if __name__ == '__main__':
    main()
