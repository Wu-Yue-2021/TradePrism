"""
XIRR 求解器端到端回归测试

用途：改动 calculate_xirr 或其网格 / 牛顿 / 二分参数后重跑，确认未退化。

做法：生成多类模拟现金流，对每组同时用
      ① calculate_xirr()        —— 生产路径（四层网格 + 阻尼牛顿 + 二分兜底）
      ② 极细网格（10 万点）基准  —— 步长 0.003，远小于实测最紧根对间距 0.039
      基准与生产使用相同的扫描范围，只比较求解器本身。

比对留在 u 域：生产返回 r，基准保留 u。因为 r = e^u - 1 在 r → -1 处分辨率有限
（1+r = 2.2e-16 时，相邻两个可表示的 r 对应的 u 相差约 0.5），在 r 域无法验证
u 域的精度。判据取 u_tolerance(r) —— 即 r 的表示精度折算到 u 域的容差。

覆盖场景：单边持有、波段进出、短线极端（1~7 天）、长跨度、极端亏损。

用法：python code_tmp/xirr/xirr_solver_test.py
"""
import math
import os
import random
import sys
from datetime import datetime, timedelta

import numpy as np

# 上溯三级到项目根目录（tools/），以便 import irr_analyzer
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from irr_analyzer import TradeAnalyzer  # noqa: E402

SEED = 20261005
N_PER_KIND = 60          # 每类场景的样本数
REF_GRID = 100000        # 基准网格点数
BASE = datetime(2026, 1, 1)


# ---------- 现金流生成 ----------

def gen_single_side(rng):
    """单边持有：多次买入 + 终值"""
    n = rng.randint(2, 8)
    span = rng.uniform(0.1, 3.0)
    ts = sorted(rng.uniform(0, span) for _ in range(n))
    amts = [-rng.uniform(1000, 5000) for _ in range(n - 1)]
    amts.append(rng.uniform(500, 50000))
    return ts, amts


def gen_swing(rng):
    """波段进出：多次买卖交替"""
    n = rng.randint(6, 40)
    span = rng.uniform(0.2, 5.0)
    ts = sorted(rng.uniform(0, span) for _ in range(n))
    amts = [-rng.uniform(1000, 5000)]
    d = 1
    for _ in range(n - 2):
        if rng.random() < 0.35:
            d *= -1
        amts.append(d * rng.uniform(500, 5000))
    amts.append(rng.uniform(500, 20000))
    return ts, amts


def gen_short_term(rng):
    """短线极端：1~7 天持仓，涨跌幅 5%~50%"""
    days = rng.randint(1, 7)
    ratio = rng.choice([1, -1]) * rng.uniform(0.05, 0.5)
    return [0.0, days / 365.0], [-10000.0, 10000.0 * (1 + ratio)]


def gen_long_span(rng):
    """长跨度：5~20 年的多次操作"""
    n = rng.randint(5, 20)
    span = rng.uniform(5.0, 20.0)
    ts = sorted(rng.uniform(0, span) for _ in range(n))
    amts = [-rng.uniform(1000, 5000) for _ in range(n - 1)]
    amts.append(rng.uniform(500, 80000))
    return ts, amts


def gen_extreme(rng):
    """极端：接近归零的亏损"""
    days = rng.randint(30, 800)
    ratio = rng.uniform(0.001, 0.3)
    return [0.0, days / 365.0], [-10000.0, 10000.0 * ratio]


KINDS = {
    '单边持有': gen_single_side,
    '波段进出': gen_swing,
    '短线极端': gen_short_term,
    '长跨度': gen_long_span,
    '极端亏损': gen_extreme,
}


# ---------- 工具 ----------

def to_cash_flows(ts, amts):
    return [(BASE + timedelta(days=t * 365), float(a)) for t, a in zip(ts, amts)]


def unpack(cfs):
    """现金流 -> (相对年份数组, 金额数组)"""
    t0 = min(t for t, _ in cfs)
    ys = np.array([(t - t0).total_seconds() / 31536000.0 for t, _ in cfs])
    amts = np.array([v for _, v in cfs])
    return ys, amts


def npv_at_u(u, ys, amts):
    """归一化 NPV（公因子恒正，符号与零点与原式一致），避免大 |u| 时溢出"""
    y_ref = ys.max() if u < 0 else ys.min()
    return float(np.exp(-(ys - y_ref) * u) @ amts)


def reference_roots(ys, amts, u_lo, u_hi):
    """在 [u_lo, u_hi] 内用极细网格求全部实根，返回 u 值（不转 r，免于表示损失）"""
    us = np.linspace(u_lo, u_hi, REF_GRID)
    vals = np.empty_like(us)
    neg = us < 0
    vals[neg] = np.exp(-np.outer(us[neg], ys - ys.max())) @ amts
    vals[~neg] = np.exp(-np.outer(us[~neg], ys - ys.min())) @ amts
    hits = np.where(np.diff(np.sign(vals)) != 0)[0]

    found = []
    for i in hits:
        lo, hi, flo = us[i], us[i + 1], vals[i]
        for _ in range(80):
            mid = (lo + hi) / 2
            fmid = npv_at_u(mid, ys, amts)
            if flo * fmid <= 0:
                hi = mid
            else:
                lo, flo = mid, fmid
        u = (lo + hi) / 2
        if not found or abs(u - found[-1]) > 1e-9:
            found.append(u)
    return found


def u_tolerance(r):
    """
    r 的表示精度折算到 u 域的容差。

    1 + r 的舍入误差来自 r 的 ulp（1 是精确的），故 Δ(1+r) = ulp(r)，
    对应 Δu = ulp(r)/(1+r)。r → -1 时该值急剧放大：1+r = 2.2e-16 时 Δu ≈ 0.5，
    即相邻两个可表示的 r 对应的 u 相差半单位 —— r 承载不了更细的 u 精度。
    求解器在 u 域求解是对的，损失发生在"把 u 转成 r 返回"这一步。
    """
    if r <= -1:
        return float('inf')
    if r == 0:
        return 1e-12
    return max(1e-12, math.ulp(r) / abs(1.0 + r))


def same_root(r, u_ref):
    """判断生产返回的 r 与基准的 u 是否指向同一个根"""
    if r <= -1:
        # u 已无法由 r 承载：只要基准的 u 舍入到 r 域也是 -1，即视为一致
        return math.exp(u_ref) - 1 <= -1
    return abs(math.log1p(r) - u_ref) <= u_tolerance(r)


def compare(prod_rs, ref_us):
    """比较生产返回的 r 集合与基准的 u 集合，返回 (漏掉的, 多出的)"""
    miss = [u for u in ref_us if not any(same_root(r, u) for r in prod_rs)]
    extra = [r for r in prod_rs if not any(same_root(r, u) for u in ref_us)]
    return miss, extra


def fmt_r(r):
    return f'{r * 100:.4f}%' if abs(r) < 1e6 else f'{r:.3e}'


def fmt_u(u):
    return fmt_r(math.exp(u) - 1) if u > -700 else '-100%'


# ---------- 主流程 ----------

def run_kind(name, gen, rng, verbose=True):
    n_bad = n_multi = n_unrepr = 0
    total_roots = 0
    problems = []

    for _ in range(N_PER_KIND):
        ts, amts = gen(rng)
        cfs = to_cash_flows(ts, amts)
        ys, amts_arr = unpack(cfs)

        analyzer = TradeAnalyzer([], 0.0, 'TEST')
        analyzer.strategic_cash_flows = cfs
        # calculate_xirr 返回 [(r, u), ...]，本测试只比较年化 r
        prod = [r for r, _ in analyzer.calculate_xirr()]

        # 基准用生产网格的实际首末点，保证比较的是求解器而非覆盖范围
        grid = analyzer._xirr_scan_grid(ys)
        ref_us = reference_roots(ys, amts_arr, grid[0], grid[-1])

        total_roots += len(prod)
        n_unrepr += sum(1 for r in prod if r <= -1)
        if len(ref_us) >= 2:
            n_multi += 1

        miss, extra = compare(prod, ref_us)
        if miss or extra:
            n_bad += 1
            if len(problems) < 4:
                problems.append((prod, ref_us, miss, extra, len(ys), float(ys[-1]), grid[0]))

    flag = '✓' if n_bad == 0 else f'✗ {n_bad} 组异常'
    print(f'  {name:<10} 根 {total_roots:>4} 个, 多根 {n_multi:>3} 组, '
          f'r=-1 无法定位 {n_unrepr:>2} 个   {flag}')
    if verbose:
        for prod, ref_us, miss, extra, n, span, u_lo in problems:
            print(f'      案例: {n} 笔, 跨度 {span:.2f} 年, 扫描下界 u={u_lo:.1f}')
            print(f'      生产 {[fmt_r(x) for x in prod]}')
            print(f'      基准 {[fmt_u(u) for u in ref_us]}')
            if miss:
                print(f'      >> 漏掉 {[fmt_u(u) for u in miss]}')
            if extra:
                print(f'      >> 多出 {[fmt_r(x) for x in extra]}')
    return n_bad


def main():
    print('=' * 72)
    print('XIRR 求解器端到端回归测试')
    print(f'  生产: calculate_xirr()   基准: {REF_GRID} 点极细网格（相同扫描范围）')
    print('  比对在 u 域，容差取 r 的表示精度（r→-1 时该容差随之放大）')
    print('=' * 72)

    rng = random.Random(SEED)
    bad_total = 0
    for name, gen in KINDS.items():
        bad_total += run_kind(name, gen, rng)

    print('=' * 72)
    print(f'结论: {"全部通过" if bad_total == 0 else f"{bad_total} 组不一致"}')


if __name__ == '__main__':
    main()