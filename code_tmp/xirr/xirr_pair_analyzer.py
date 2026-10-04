"""
XIRR 相邻根对分析器

输入：code_tmp/xirr/xirr_root_sample.csv（由 xirr_root_sampler.py 生成，列: case_id, n_roots, u）

与 xirr_grid_optimizer.py 的分工：
  optimizer 统计"根的绝对位置分布"      -> 回答「扫描范围要盖到多宽」
  本脚本  统计"相邻根对的间距及其位置"    -> 回答「密区要多密、密区盖到哪」

为什么后者才是密区依据：网格漏根的充要条件是"两个根之间没有网格点"；前者仅供参考。
孤立单根只要网格点跨过它两侧，符号必然变化，与步长大小无关——大步长照样检出。
只有挤在一起的根对才需要细网格。

输出：
  1. 相邻根间距分布
  2. 按候选步长评估会漏哪些根对、它们落在哪（验证"小间距根对集中在中间区"）
  3. 密区边界 K 扫描：密区 / 疏区各自的最小根对间距
  4. 由疏区最小间距反推的推荐方案与总点数
  5. 最紧密的若干对根（人工复核用）
"""
import csv
from collections import defaultdict

import numpy as np

CSV_PATH = 'code_tmp/xirr/xirr_root_sample.csv'
SCAN_LIMIT = 150.0      # 扫描总边界 ±
DENSE_PERCENTILE = 1.0  # 密区步长 = 相邻根间距的该分位 / 2


def load_pairs(path):
    """读取采样结果，返回 (案例字典, 全部相邻根对列表)"""
    cases = defaultdict(list)
    with open(path, encoding='utf-8') as f:
        for row in csv.DictReader(f):
            cases[int(row['case_id'])].append(float(row['u']))

    pairs = []
    for us in cases.values():
        us.sort()
        for i in range(len(us) - 1):
            pairs.append((us[i], us[i + 1], us[i + 1] - us[i]))
    return cases, pairs


def report_basic(cases, pairs):
    n_root = sum(len(v) for v in cases.values())
    n_multi = sum(1 for v in cases.values() if len(v) >= 2)
    print(f'样本: {len(cases)} 组案例, {n_root} 个根')
    print(f'  单根案例 {len(cases) - n_multi} 组, 多根案例 {n_multi} 组 '
          f'({n_multi / len(cases) * 100:.1f}%)')
    print(f'  相邻根对 {len(pairs)} 对\n')


def report_gaps(pairs):
    """相邻根间距的分位数分布"""
    gaps = np.array([g for _, _, g in pairs])
    print('[1] 相邻根间距分布')
    for p in [0, 1, 5, 10, 25, 50, 75, 100]:
        print(f'    {p:>3}.0% : {np.percentile(gaps, p):>10.4f}')
    return gaps


def report_by_step(pairs, steps=(0.3, 0.5, 1.0, 2.0, 3.0, 5.0)):
    """用候选步长评估漏根，并检验这些根对的位置分布"""
    print('\n[2] 按步长评估：会漏哪些根对，它们落在 u 的什么位置')
    print(f'    {"步长h":>7} {"漏根对":>8} {"占比":>8}   {"中点分布":>32}')
    for h in steps:
        missed = [(lo, hi, g) for lo, hi, g in pairs if g < h]
        if not missed:
            print(f'    {h:>7.1f} {0:>8} {0.0:>7.1f}%   ——')
            continue
        mids = np.array([(lo + hi) / 2 for lo, hi, _ in missed])
        inside = np.mean(np.abs(mids) <= 6) * 100
        print(f'    {h:>7.1f} {len(missed):>8} {len(missed) / len(pairs) * 100:>7.1f}%   '
              f'[{mids.min():>7.2f}, {mids.max():>7.2f}]  |中点|<=6 占 {inside:.0f}%')


def report_boundary(pairs, ks=(3, 4, 5, 6, 8, 10)):
    """扫描密区边界 K：密区与疏区各自的最小根对间距"""
    print('\n[3] 密区边界 K 扫描（密区 / 疏区各自的最小根对间距）')
    print(f'    {"K":>4} {"密区根对":>9} {"密区最小间距":>13} {"疏区根对":>9} {"疏区最小间距":>13}')
    for k in ks:
        dense = [g for lo, hi, g in pairs if -k <= (lo + hi) / 2 <= k]
        sparse = [g for lo, hi, g in pairs if not (-k <= (lo + hi) / 2 <= k)]
        d_min = f'{min(dense):.4f}' if dense else '—'
        s_min = f'{min(sparse):.4f}' if sparse else '—'
        print(f'    {k:>4} {len(dense):>9} {d_min:>13} {len(sparse):>9} {s_min:>13}')


def recommend(pairs, h_dense, ks=(4, 5, 6, 8, 10)):
    """由疏区最小间距反推各 K 下的可行方案与总点数"""
    print(f'\n[4] 推荐方案（密区步长 {h_dense:.3f}，疏区步长取疏区最小间距的一半）')
    print(f'    {"K":>4} {"h_疏":>7} {"密区点":>8} {"疏区点":>8} {"总点数":>8}')
    for k in ks:
        sparse = [g for lo, hi, g in pairs if abs((lo + hi) / 2) > k]
        if not sparse:
            continue
        h_sparse = min(sparse) / 2
        n_dense = int(2 * k / h_dense) + 1
        n_sparse = int(2 * (SCAN_LIMIT - k) / h_sparse) + 2
        print(f'    {k:>4} {h_sparse:>7.3f} {n_dense:>8} {n_sparse:>8} {n_dense + n_sparse:>8}')
    print(f'\n    参照: 全域均匀覆盖 ±{SCAN_LIMIT:.0f}、步长 {h_dense:.3f} '
          f'需 {int(2 * SCAN_LIMIT / h_dense)} 点')


def show_tightest(pairs, n=12):
    """列出最紧密的 n 对根，用于确认它们的位置"""
    print(f'\n[5] 间距最小的 {n} 对根（确认是否落在中间区）')
    print(f'    {"左根":>10} {"右根":>10} {"间距":>9} {"中点":>10}')
    for lo, hi, g in sorted(pairs, key=lambda x: x[2])[:n]:
        print(f'    {lo:>10.4f} {hi:>10.4f} {g:>9.4f} {(lo + hi) / 2:>10.4f}')


def main():
    cases, pairs = load_pairs(CSV_PATH)
    report_basic(cases, pairs)
    gaps = report_gaps(pairs)
    report_by_step(pairs)
    report_boundary(pairs)
    recommend(pairs, np.percentile(gaps, DENSE_PERCENTILE) / 2)
    show_tightest(pairs)


if __name__ == '__main__':
    main()
