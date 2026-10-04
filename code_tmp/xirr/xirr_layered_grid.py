"""
XIRR 分层网格设计器

输入：code_tmp/xirr/xirr_root_sample.csv（由 xirr_root_sampler.py 生成）

多层依据：非均匀网格不必只有"密 + 疏"两层。
  对每个 |u| 环带统计"该带内根对的最小间距"——它决定该带所需的最小步长。
  越靠外的带里，根对越孤立（间距越大），步长就能放得越宽，于是可以逐层放大。
  层内步长只受本层最小间距约束，与更内层的紧密根对无关。

输出：
  1. 各 |u| 环带的根对数、最小间距、可行步长
  2. 若干候选分层方案（2/3/4/5 层）的总点数对比，含 100% 不漏与允许漏根两档
  3. 逐层放大倍数的观察（判断多层收益是否递减）
"""
import csv
from collections import defaultdict

import numpy as np

CSV_PATH = 'code_tmp/xirr/xirr_root_sample.csv'
SCAN_LIMIT = 150.0

# 环带边界（|u| 的值），用于观察"可行步长随 |u| 如何增长"
BANDS = [0, 0.5, 1, 1.5, 2, 3, 4, 5, 6, 8, 10, 15, 20, 30, 50, 80, 120, 150.0]

# 候选分层方案：(各层外边界, 密区步长的分位口径)
#   分位口径 = 0 表示"该层最小间距 / 2"，100 表示"允许漏掉最紧的那些"
SCHEMES = {
    '两层  10': [10, 150.0],
    '三层  10/80': [10, 80, 150.0],
    '三层  3/10': [3, 10, 150.0],
    '四层  3/10/80': [3, 10, 80, 150.0],
    '四层  2/5/10': [2, 5, 10, 150.0],
    '五层  3/5/10/80': [3, 5, 10, 80, 150.0],
    '五层  1/2/5/10': [1, 2, 5, 10, 150.0],
    '六层  2/3/5/10/80': [2, 3, 5, 10, 80, 150.0],
    '七层  1/2/3/5/10/80': [1, 2, 3, 5, 10, 80, 150.0],
}


def load_pairs(path):
    """读取采样结果，返回全部相邻根对 (左根, 右根, 间距)"""
    cases = defaultdict(list)
    with open(path, encoding='utf-8') as f:
        for row in csv.DictReader(f):
            cases[int(row['case_id'])].append(float(row['u']))
    pairs = []
    for us in cases.values():
        us.sort()
        for i in range(len(us) - 1):
            pairs.append((us[i], us[i + 1], us[i + 1] - us[i]))
    return pairs


def band_stats(pairs, bands):
    """统计每个 |u| 环带内的根对数与最小间距"""
    rows = []
    for a, b in zip(bands, bands[1:]):
        gs = [g for lo, hi, g in pairs if a < abs((lo + hi) / 2) <= b]
        rows.append((a, b, len(gs), min(gs) if gs else None))
    return rows


def report_bands(rows):
    print('[1] 各 |u| 环带的可行步长')
    print(f'    {"|u| 区间":>16} {"根对数":>8} {"最小间距":>10} {"可行步长":>10} {"相对内层放大":>12}')
    prev_h = None
    for a, b, n, g in rows:
        if g is None:
            print(f'    {f"({a}, {b}]":>16} {n:>8} {"—":>10} {"无约束":>10} {"—":>12}')
            continue
        h = g / 2
        ratio = f'{h / prev_h:.2f}x' if prev_h else '—'
        print(f'    {f"({a}, {b}]":>16} {n:>8} {g:>10.4f} {h:>10.4f} {ratio:>12}')
        prev_h = h


def layer_step(pairs, lo, hi):
    """层 (lo, hi] 内所需的步长（该层最小间距的一半）；层内无根对时返回 None"""
    gs = [g for a, b, g in pairs if lo < abs((a + b) / 2) <= hi]
    return min(gs) / 2 if gs else None


def scheme_cost(pairs, edges, fallback_ratio=4.0, fallback_cap=20.0):
    """按给定分段边界计算总点数；无根对的层按前一层步长的 fallback_ratio 倍放大"""
    total = 0.0
    prev_h = None
    detail = []
    inner = 0.0
    for outer in edges:
        h = layer_step(pairs, inner, outer)
        if h is None:
            h = min(prev_h * fallback_ratio, fallback_cap) if prev_h else fallback_cap
        width = 2 * (outer - inner)          # 双侧
        n = width / h
        total += n
        detail.append((inner, outer, h, n))
        prev_h = h
        inner = outer
    return total, detail


def main():
    pairs = load_pairs(CSV_PATH)
    print(f'样本: {len(pairs)} 对相邻根\n')

    rows = band_stats(pairs, BANDS)
    report_bands(rows)

    print('\n[2] 候选分层方案（每层步长 = 该层最小间距 / 2，100% 不漏）')
    print(f'    {"方案":<24} {"层数":>5} {"总点数":>9}   各层步长')
    for name, edges in SCHEMES.items():
        total, detail = scheme_cost(pairs, edges)
        steps = ' / '.join(f'{h:.3f}' for _, _, h, _ in detail)
        print(f'    {name:<24} {len(edges):>5} {total:>9.0f}   {steps}')

    print('\n[3] 若允许漏掉每层最紧的 1%（步长改取层内间距的 1% 分位 / 2）')
    print(f'    {"方案":<24} {"层数":>5} {"总点数":>9}   各层步长')
    for name, edges in SCHEMES.items():
        total = 0.0
        inner = 0.0
        prev_h = None
        steps = []
        for outer in edges:
            gs = [g for a, b, g in pairs if inner < abs((a + b) / 2) <= outer]
            h = (np.percentile(gs, 1) / 2) if gs else min((prev_h or 0.5) * 4, 20.0)
            total += 2 * (outer - inner) / h
            steps.append(f'{h:.3f}')
            prev_h = h
            inner = outer
        print(f'    {name:<24} {len(edges):>5} {total:>9.0f}   ' + ' / '.join(steps))

    print(f'\n    参照: 全域均匀 ±{SCAN_LIMIT:.0f}')
    print(f'      100% 不漏需步长 {min(g for _, _, g in pairs) / 2:.4f} '
          f'-> {2 * SCAN_LIMIT / (min(g for _, _, g in pairs) / 2):.0f} 点')
    print(f'      允许漏 1% 需步长 {np.percentile([g for _, _, g in pairs], 1) / 2:.4f} '
          f'-> {2 * SCAN_LIMIT / (np.percentile([g for _, _, g in pairs], 1) / 2):.0f} 点')


if __name__ == '__main__':
    main()
