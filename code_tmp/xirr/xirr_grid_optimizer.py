"""
XIRR 非均匀网格划分求解器

输入：code_tmp/xirr/xirr_root_sample.csv（由 xirr_root_sampler.py 生成）

思路：划分方案由两个可观测量决定，而不是拍脑袋定阈值——
  1. 密区范围  <- 多根实测落在哪些 u 上（密区必须覆盖它们的极值）
  2. 密区步长  <- 相邻根间距的低分位（步长必须小于它才能分辨两个根）
  3. 疏区步长  <- 密区步长的固定倍数（依据：稀疏区 NPV 单调，至多一根，
                  任意步长只要跨越根的两侧就必然变号，无需细网）

输出：推荐的分段方案与总点数。
"""
import csv
from collections import defaultdict

import numpy as np

CSV_PATH = 'code_tmp/xirr/xirr_root_sample.csv'
SCAN_LIMIT = 150.0      # 扫描总边界 ±（覆盖新股首日 +44% 的 u=133）
SPARSE_FACTOR = 10.0    # 疏区步长 = 密区步长 × 该倍数
GAP_PERCENTILE = 1.0    # 密区步长取相邻根间距的该分位，再除以 2


def load_cases(path):
    """读取采样结果，按案例分组，返回排序后的根列表"""
    cases = defaultdict(list)
    with open(path, encoding='utf-8') as f:
        for row in csv.DictReader(f):
            cases[int(row['case_id'])].append(float(row['u']))
    return [sorted(v) for v in cases.values()]


def derive_params(cases):
    """从采样数据推导密区范围与步长"""
    multi = [v for v in cases if len(v) >= 2]
    multi_roots = [u for v in multi for u in v]

    dense_lo, dense_hi = min(multi_roots), max(multi_roots)

    gaps = np.array([v[i + 1] - v[i] for v in multi for i in range(len(v) - 1)])
    h_dense = np.percentile(gaps, GAP_PERCENTILE) / 2

    return multi, multi_roots, gaps, dense_lo, dense_hi, h_dense


def build_grid(dense_lo, dense_hi, h_dense, limit=SCAN_LIMIT, sparse_factor=SPARSE_FACTOR):
    """构造非均匀网格：密区用 h_dense，两侧疏区用 h_dense × sparse_factor"""
    h_sparse = h_dense * sparse_factor

    n_left = max(1, int(np.ceil((dense_lo + limit) / h_sparse)))
    n_dense = max(1, int(np.ceil((dense_hi - dense_lo) / h_dense)))
    n_right = max(1, int(np.ceil((limit - dense_hi) / h_sparse)))

    left = np.linspace(-limit, dense_lo, n_left + 1)[:-1]
    dense = np.linspace(dense_lo, dense_hi, n_dense + 1)[:-1]
    right = np.linspace(dense_hi, limit, n_right + 1)
    return np.concatenate([left, dense, right]), (n_left, n_dense, n_right)


def main():
    cases = load_cases(CSV_PATH)
    n_root_total = sum(len(v) for v in cases)
    multi, multi_roots, gaps, dense_lo, dense_hi, h_dense = derive_params(cases)
    single = [v for v in cases if len(v) == 1]

    print(f'样本: {len(cases)} 组案例, {n_root_total} 个根')
    print(f'  单根案例 {len(single)} 组, 多根案例 {len(multi)} 组, '
          f'多根贡献 {len(multi_roots)} 个根\n')

    # ---- 1. 密区范围 ----
    print('[1] 密区范围 (多根实测落在何处)')
    print(f'    极值: [{dense_lo:.4f}, {dense_hi:.4f}]')
    for p in [0.1, 1, 5, 50, 95, 99, 99.9]:
        print(f'    {p:>5.1f}% 分位: {np.percentile(multi_roots, p):>9.4f}')
    print(f'    单根案例的 |u| 中位数: {np.median([abs(v[0]) for v in single]):.4f}'
          f'  (说明单根遍布全轴, 不构成密区约束)')

    # ---- 2. 密区步长 ----
    print(f'\n[2] 相邻根间距 (决定密区步长)')
    print(f'    共 {len(gaps)} 对相邻根')
    for p in [0, 0.01, 0.1, 1, 5, 25, 50]:
        print(f'    {p:>5.2f}% 分位: {np.percentile(gaps, p):.6f}')
    print(f'    => 密区步长取 {GAP_PERCENTILE}% 分位的一半 = {h_dense:.6f}')

    # ---- 3. 网格与点数 ----
    h_sparse = h_dense * SPARSE_FACTOR
    grid, (n_left, n_dense, n_right) = build_grid(dense_lo, dense_hi, h_dense)
    print(f'\n[3] 划分方案')
    print(f'    左疏区 [-{SCAN_LIMIT:.0f}, {dense_lo:.4f}]  步长 {h_sparse:.4f}  {n_left:>4} 段')
    print(f'    密  区 [{dense_lo:.4f}, {dense_hi:.4f}]  步长 {h_dense:.6f}  {n_dense:>4} 段')
    print(f'    右疏区 [{dense_hi:.4f}, {SCAN_LIMIT:.0f}]  步长 {h_sparse:.4f}  {n_right:>4} 段')
    print(f'    总点数 {len(grid)}  (均匀网格同精度需 {int(2*SCAN_LIMIT/h_dense)} 点)')

    # ---- 4. 回测: 用该网格能否检出样本中的全部根 ----
    print(f'\n[4] 回测: 网格是否漏根')
    # 根被检出 <=> 它两侧的网格点异号; 这里只做几何判定——相邻根之间是否有网格点
    missed = 0
    for v in multi:
        for i in range(len(v) - 1):
            if not np.any((grid > v[i]) & (grid < v[i + 1])):
                missed += 1
    print(f'    相邻根对总数 {len(gaps)}, 其中两根之间无网格点的: {missed}')
    print(f'    (疏区单根的情形由单调性保证, 无需网格点夹住)')


if __name__ == '__main__':
    main()
