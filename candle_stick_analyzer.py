"""
股票日线数据分析工具
分析K线形态、开收关系、成交量特征等
"""

import sys
import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import re
import argparse

from data_utils import load_huatai_data, calculate_indicators

# 设置UTF-8编码
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


class CandleStickAnalyzer:
    """K线股性分析器"""

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.data: Optional[pd.DataFrame] = None
        self.df: Optional[pd.DataFrame] = None
        self.stock_name: Optional[str] = None  # 股票名称
        self.chart_type: Optional[str] = None  # K线类型（日线/周线/月线等）

    def load_data(self) -> bool:
        """导入华泰导出的日线数据"""
        df, meta = load_huatai_data(self.file_path)

        if df is None:
            print("❌ 导入数据失败")
            return False

        self.stock_name = meta.get('stock_name')
        self.chart_type = meta.get('chart_type')
        self.df = calculate_indicators(df)

        print(f"✅ 成功导入 {len(self.df)} 条数据")
        print(f"📅 日期范围: {self.df['date'].min().date()} 至 {self.df['date'].max().date()}")
        print(f"💰 价格区间: {self.df['low'].min():.2f} - {self.df['high'].max():.2f}")
        return True

    def analyze_open_close_patterns(self, start_date: Optional[str] = None, end_date: Optional[str] = None):
        """分析开收关系概率分布"""
        df = self._filter_by_date(start_date, end_date)

        print("\n" + "="*60)
        print(f"📊 {self.stock_name} 开收关系概率分布分析")
        print("="*60)
        print(f"分析区间: {df['date'].min().date()} 至 {df['date'].max().date()} ({len(df)}个交易日)")
        print("-"*60)

        # 统计各类形态
        pattern_stats = df['open_close_pattern'].value_counts()
        total = len(df)

        for pattern in ['低开高走', '低开低走', '高开高走', '高开低走']:
            count = pattern_stats.get(pattern, 0)
            pct = count / total * 100 if total > 0 else 0
            bar = "█" * int(pct / 2)
            print(f"{pattern:8s}: {count:3d}次 ({pct:5.2f}%) {bar}")

        # 更详细的分析
        print("\n📈 各形态次日表现:")
        print("-"*60)
        print(f"{'形态':<8} {'次日平均涨幅':<12} {'次日上涨概率':<12} {'次日平均涨幅(涨时)'}")
        print("-"*60)

        for pattern in ['低开高走', '低开低走', '高开高走', '高开低走']:
            pattern_df = df[df['open_close_pattern'] == pattern]
            if len(pattern_df) > 0:
                next_change = pattern_df['next_pct_change'].dropna()
                if len(next_change) > 0:
                    avg_next = next_change.mean()
                    up_prob = (next_change > 0).sum() / len(next_change) * 100
                    up_avg = next_change[next_change > 0].mean()
                    print(f"{pattern:<8} {avg_next:>10.2f}%    {up_prob:>10.1f}%        {up_avg:>10.2f}%")

    def analyze_big_candles(self, threshold: float = 5.0, start_date: Optional[str] = None, end_date: Optional[str] = None):
        """分析大阳线/大阴线的次日走势

        Args:
            threshold: 大阳线/大阴线的涨跌幅阈值(%)
            start_date: 开始日期
            end_date: 结束日期
        """
        df = self._filter_by_date(start_date, end_date).reset_index(drop=True)

        print("\n" + "="*60)
        print(f"📊 {self.stock_name} 大阳线/大阴线次日走势分析 (阈值: ±{threshold}%)")
        print("="*60)
        print(f"分析区间: {df['date'].min().date()} 至 {df['date'].max().date()} ({len(df)}个交易日)")
        print("-"*60)

        # 根据阈值判断大阳线/大阴线
        is_big_positive = df['pct_change'] > threshold
        is_big_negative = df['pct_change'] < -threshold

        # 辅助函数：计算从某个位置开始连续上涨/下跌的天数
        def calc_consecutive_stats(idx, check_up=True):
            consecutive_list = []
            consecutive = 0
            for i in range(idx + 1, len(df)):
                pct = df.loc[i, 'pct_change']
                if pd.isna(pct):
                    break
                if check_up:
                    if pct > 0:  # type: ignore[operator]
                        consecutive += 1
                    else:
                        break
                else:
                    if pct < 0:  # type: ignore[operator]
                        consecutive += 1
                    else:
                        break
            return consecutive

        # 大阳线分析
        big_positive_indices = df[is_big_positive].index.tolist()
        if len(big_positive_indices) > 0:
            big_positive_df = df.loc[big_positive_indices]
            next_changes = big_positive_df['next_pct_change'].dropna()

            print(f"\n☀️  大阳线(涨幅>{threshold}%)共 {len(big_positive_indices)} 次 ({len(big_positive_indices)/len(df)*100:.1f}%)")
            print(f"   次日平均涨跌: {next_changes.mean():.2f}%")
            print(f"   次日上涨次数: {(next_changes > 0).sum()} / {len(next_changes)} ({(next_changes > 0).sum() / len(next_changes) * 100:.1f}%)")

            # 统计大阳线后连续上涨天数
            consecutive_up_list = []
            consecutive_down_list = []

            for idx in big_positive_indices:
                if idx < len(df) - 1:
                    # 统计后续连续上涨天数
                    up_days = calc_consecutive_stats(idx, check_up=True)
                    # 统计后续连续下跌天数（从次日判断是否下跌开始）
                    if idx + 1 < len(df) and df.loc[idx + 1, 'pct_change'] < 0:  # type: ignore[operator]
                        down_days = 1 + calc_consecutive_stats(idx + 1, check_up=False)
                    else:
                        down_days = 0
                    consecutive_up_list.append(up_days)
                    consecutive_down_list.append(down_days)

            if consecutive_up_list:
                print(f"   后续平均连续上涨: {np.mean(consecutive_up_list):.1f}天, 最大: {max(consecutive_up_list)}天")
            if consecutive_down_list:
                down_nonzero = [x for x in consecutive_down_list if x > 0]
                if down_nonzero:
                    print(f"   后续平均连续下跌: {np.mean(down_nonzero):.1f}天, 最大: {max(consecutive_down_list)}天")

        # 大阴线分析
        big_negative_indices = df[is_big_negative].index.tolist()
        if len(big_negative_indices) > 0:
            big_negative_df = df.loc[big_negative_indices]
            next_changes = big_negative_df['next_pct_change'].dropna()

            print(f"\n☁️  大阴线(跌幅<-{threshold}%)共 {len(big_negative_indices)} 次 ({len(big_negative_indices)/len(df)*100:.1f}%)")
            print(f"   次日平均涨跌: {next_changes.mean():.2f}%")
            print(f"   次日上涨次数: {(next_changes > 0).sum()} / {len(next_changes)} ({(next_changes > 0).sum() / len(next_changes) * 100:.1f}%)")

            # 统计大阴线后连续下跌天数
            consecutive_down_list = []
            consecutive_up_list = []

            for idx in big_negative_indices:
                if idx < len(df) - 1:
                    # 统计后续连续下跌天数
                    down_days = calc_consecutive_stats(idx, check_up=False)
                    # 统计后续连续上涨天数（从次日判断是否上涨开始）
                    if idx + 1 < len(df) and df.loc[idx + 1, 'pct_change'] > 0:  # type: ignore[operator]
                        up_days = 1 + calc_consecutive_stats(idx + 1, check_up=True)
                    else:
                        up_days = 0
                    consecutive_down_list.append(down_days)
                    consecutive_up_list.append(up_days)

            if consecutive_down_list:
                print(f"   后续平均连续下跌: {np.mean(consecutive_down_list):.1f}天, 最大: {max(consecutive_down_list)}天")
            if consecutive_up_list:
                up_nonzero = [x for x in consecutive_up_list if x > 0]
                if up_nonzero:
                    print(f"   后续平均连续上涨: {np.mean(up_nonzero):.1f}天, 最大: {max(consecutive_up_list)}天")

    def analyze_volume_patterns(self, start_date: Optional[str] = None, end_date: Optional[str] = None):
        """分析成交量相关模式"""
        df = self._filter_by_date(start_date, end_date)

        print("\n" + "="*60)
        print(f"📊 {self.stock_name} 量价特征分析")
        print("="*60)
        print(f"分析区间: {df['date'].min().date()} 至 {df['date'].max().date()} ({len(df)}个交易日)")
        print("-"*60)

        # 放量缩量定义
        volume_high = (df['volume_ratio'] > 1) & (df['volume_diff'] >= 0)   # 相比昨天放量且量比大于1
        volume_low = (df['volume_ratio'] < 1) & (df['volume_diff'] <= 0)

        print(f"\n📈 放量(成交量增加且大于五日平均)分析:")
        high_vol_df = df[volume_high]
        if len(high_vol_df) > 0:
            next_changes = high_vol_df['next_pct_change'].dropna()
            print(f"   出现次数: {len(high_vol_df)} ({len(high_vol_df)/len(df)*100:.1f}%)")
            print(f"   当日平均涨幅: {high_vol_df['pct_change'].mean():.2f}%")
            print(f"   次日平均涨幅: {next_changes.mean():.2f}%")
            print(f"   次日上涨概率: {(next_changes > 0).sum() / len(next_changes) * 100:.1f}%")

        print(f"\n📉 缩量(成交量减少且小于五日平均)分析:")
        low_vol_df = df[volume_low]
        if len(low_vol_df) > 0:
            next_changes = low_vol_df['next_pct_change'].dropna()
            print(f"   出现次数: {len(low_vol_df)} ({len(low_vol_df)/len(df)*100:.1f}%)")
            print(f"   当日平均涨幅: {low_vol_df['pct_change'].mean():.2f}%")
            print(f"   次日平均涨幅: {next_changes.mean():.2f}%")
            print(f"   次日上涨概率: {(next_changes > 0).sum() / len(next_changes) * 100:.1f}%")

        # 量价配合
        print(f"\n💰 量价配合分析:")
        price_up_volume_high = df[(df['pct_change'] > 0) & volume_high]
        price_up_volume_low = df[(df['pct_change'] > 0) & volume_low]
        price_down_volume_high = df[(df['pct_change'] < 0) & volume_high]
        price_down_volume_low = df[(df['pct_change'] < 0) & volume_low]

        print(f"   放量上涨: {len(price_up_volume_high)}次, 次日平均 {price_up_volume_high['next_pct_change'].mean():.2f}%")
        print(f"   缩量上涨: {len(price_up_volume_low)}次, 次日平均 {price_up_volume_low['next_pct_change'].mean():.2f}%")
        print(f"   放量下跌: {len(price_down_volume_high)}次, 次日平均 {price_down_volume_high['next_pct_change'].mean():.2f}%")
        print(f"   缩量下跌: {len(price_down_volume_low)}次, 次日平均 {price_down_volume_low['next_pct_change'].mean():.2f}%")

    def analyze_gap_patterns(self, start_date: Optional[str] = None, end_date: Optional[str] = None):
        """分析跳空模式"""
        df = self._filter_by_date(start_date, end_date)

        print("\n" + "="*60)
        print(f"📊 {self.stock_name} 跳空分析")
        print("="*60)
        print(f"分析区间: {df['date'].min().date()} 至 {df['date'].max().date()} ({len(df)}个交易日)")
        print("-"*60)

        gap_up = df['gap_pct'] > 2  # 跳空高开2%+
        gap_down = df['gap_pct'] < -2  # 跳空低开2%+

        gap_up_df = df[gap_up]
        gap_down_df = df[gap_down]

        print(f"\n   跳空高开(>2%): {len(gap_up_df)}次")
        if len(gap_up_df) > 0:
            print(f"   当日平均涨幅: {gap_up_df['pct_change'].mean():.2f}%")
            print(f"   收盘上涨: {(gap_up_df['pct_change'] > 0).sum()}次 ({(gap_up_df['pct_change'] > 0).sum()/len(gap_up_df)*100:.1f}%)")
            next_changes = gap_up_df['next_pct_change'].dropna()
            print(f"   次日平均涨幅: {next_changes.mean():.2f}%")
            print(f"   次日上涨概率: {(next_changes > 0).sum() / len(next_changes) * 100:.1f}%")

        print(f"\n   跳空低开(<-2%): {len(gap_down_df)}次")
        if len(gap_down_df) > 0:
            print(f"   当日平均涨幅: {gap_down_df['pct_change'].mean():.2f}%")
            print(f"   收盘下跌: {(gap_down_df['pct_change'] < 0).sum()}次 ({(gap_down_df['pct_change'] < 0).sum()/len(gap_down_df)*100:.1f}%)")
            next_changes = gap_down_df['next_pct_change'].dropna()
            print(f"   次日平均涨幅: {next_changes.mean():.2f}%")
            print(f"   次日上涨概率: {(next_changes > 0).sum() / len(next_changes) * 100:.1f}%")

    def analyze_shadow_patterns(self, upper_threshold: float = 1.0, lower_threshold: float = 1.0,
                                start_date: Optional[str] = None, end_date: Optional[str] = None):
        """分析上影线/下影线模式与次日走势的关系

        Args:
            upper_threshold: 上影线阈值（相对于实体的倍数）
            lower_threshold: 下影线阈值（相对于实体的倍数）
            start_date: 开始日期
            end_date: 结束日期
        """
        df = self._filter_by_date(start_date, end_date)

        print("\n" + "="*60)
        print(f"📊 {self.stock_name} 上影线/下影线分析 (阈值: >={upper_threshold}倍实体)")
        print("="*60)
        print(f"分析区间: {df['date'].min().date()} 至 {df['date'].max().date()} ({len(df)}个交易日)")
        print("-"*60)

        # 长上影线、长下影线判断
        long_upper_shadow = df['upper_shadow_ratio'] >= upper_threshold
        long_lower_shadow = df['lower_shadow_ratio'] >= lower_threshold

        # 上影线分析
        upper_shadow_df = df[long_upper_shadow]
        if len(upper_shadow_df) > 0:
            next_changes = upper_shadow_df['next_pct_change'].dropna()
            print(f"\n📈 长上影线(>={upper_threshold}倍实体)共 {len(upper_shadow_df)} 次 ({len(upper_shadow_df)/len(df)*100:.1f}%)")
            print(f"   当日平均涨跌: {upper_shadow_df['pct_change'].mean():.2f}%")
            print(f"   次日平均涨跌: {next_changes.mean():.2f}%")
            print(f"   次日上涨次数: {(next_changes > 0).sum()} / {len(next_changes)} ({(next_changes > 0).sum() / len(next_changes) * 100:.1f}%)")

            # 区分阳线和阴线的上影线
            upper_shadow_positive = upper_shadow_df[upper_shadow_df['body'] > 0]
            upper_shadow_negative = upper_shadow_df[upper_shadow_df['body'] < 0]

            if len(upper_shadow_positive) > 0:
                next_changes_p = upper_shadow_positive['next_pct_change'].dropna()
                print(f"   └─ 阳线长上影: {len(upper_shadow_positive)}次, 次日平均 {next_changes_p.mean():.2f}%")
            if len(upper_shadow_negative) > 0:
                next_changes_n = upper_shadow_negative['next_pct_change'].dropna()
                print(f"   └─ 阴线长上影: {len(upper_shadow_negative)}次, 次日平均 {next_changes_n.mean():.2f}%")

        # 下影线分析
        lower_shadow_df = df[long_lower_shadow]
        if len(lower_shadow_df) > 0:
            next_changes = lower_shadow_df['next_pct_change'].dropna()
            print(f"\n📉 长下影线(>={lower_threshold}倍实体)共 {len(lower_shadow_df)} 次 ({len(lower_shadow_df)/len(df)*100:.1f}%)")
            print(f"   当日平均涨跌: {lower_shadow_df['pct_change'].mean():.2f}%")
            print(f"   次日平均涨跌: {next_changes.mean():.2f}%")
            print(f"   次日上涨次数: {(next_changes > 0).sum()} / {len(next_changes)} ({(next_changes > 0).sum() / len(next_changes) * 100:.1f}%)")

            # 区分阳线和阴线的下影线
            lower_shadow_positive = lower_shadow_df[lower_shadow_df['body'] > 0]
            lower_shadow_negative = lower_shadow_df[lower_shadow_df['body'] < 0]

            if len(lower_shadow_positive) > 0:
                next_changes_p = lower_shadow_positive['next_pct_change'].dropna()
                print(f"   └─ 阳线长下影: {len(lower_shadow_positive)}次, 次日平均 {next_changes_p.mean():.2f}%")
            if len(lower_shadow_negative) > 0:
                next_changes_n = lower_shadow_negative['next_pct_change'].dropna()
                print(f"   └─ 阴线长下影: {len(lower_shadow_negative)}次, 次日平均 {next_changes_n.mean():.2f}%")

        # 上下影线组合
        both_shadow_df = df[long_upper_shadow & long_lower_shadow]
        if len(both_shadow_df) > 0:
            next_changes = both_shadow_df['next_pct_change'].dropna()
            print(f"\n🔄 上下影线都较长共 {len(both_shadow_df)} 次 ({len(both_shadow_df)/len(df)*100:.1f}%)")
            print(f"   当日平均涨跌: {both_shadow_df['pct_change'].mean():.2f}%")
            print(f"   次日平均涨跌: {next_changes.mean():.2f}%")
            print(f"   次日上涨次数: {(next_changes > 0).sum()} / {len(next_changes)} ({(next_changes > 0).sum() / len(next_changes) * 100:.1f}%)")

    def show_recent_data(self, days: int = 10):
        """显示最近N天的数据"""
        if self.df is None:
            print("❌ 请先导入数据!")
            return
        print("\n" + "="*100)
        print(f"📋 {self.stock_name} 最近 {days} 个交易日数据")
        print("="*100)
        recent = self.df.tail(days).copy()
        for _, row in recent.iterrows():
            pct_str = f"{row['pct_change']:+.2f}%" if not pd.isna(row['pct_change']) else "N/A"
            vol_ratio = f"{row['volume_ratio']:.2f}" if not pd.isna(row['volume_ratio']) else "N/A"
            pattern = row['open_close_pattern']
            print(f"{row['date'].date()} | 开:{row['open']:>6.2f} 高:{row['high']:>6.2f} "
                  f"低:{row['low']:>6.2f} 收:{row['close']:>6.2f} | "
                  f"涨跌:{pct_str:>7} 量比:{vol_ratio:>5} {pattern}")

    def _filter_by_date(self, start_date: Optional[str] = None, end_date: Optional[str] = None) -> pd.DataFrame:
        """按日期过滤数据"""
        if self.df is None:
            return pd.DataFrame()
        df = self.df.copy()
        if start_date:
            df = df[df['date'] >= pd.to_datetime(start_date)]
        if end_date:
            df = df[df['date'] <= pd.to_datetime(end_date)]
        return df


def print_menu(stock_name: Optional[str] = None):
    """打印菜单"""
    print("\n" + "="*60)
    title = f"📈 {stock_name} K线分析" if stock_name else "📈 K线股性分析工具"
    print(title)
    print("="*60)
    print("1. 导入数据文件")
    print("2. 查看最近数据")
    print("3. 开收关系分析 (低开高走/低开低走/高开高走/高开低走)")
    print("4. 大阳线/大阴线分析")
    print("5. 量价特征分析")
    print("6. 跳空分析")
    print("7. 影线分析")
    print("8. 综合分析(以上所有)")
    print("0. 退出")
    print("-"*60)


def main():
    """主函数"""
    analyzer = None
    current_file = None
    # 解析命令行参数
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
        if Path(file_path).exists():
            print(f"\n📂 正在导入文件: {file_path}")
            analyzer = CandleStickAnalyzer(file_path)
            if analyzer.load_data():
                # 设置窗口标题
                import os
                os.system(f'title {analyzer.stock_name}_{analyzer.chart_type}分析')

                current_file = file_path

        else:
            print(f"❌ 文件不存在: {file_path}")
            return

    # 交互式模式
    while True:
        print_menu(analyzer.stock_name if analyzer else None)
        choice = input("请选择操作 [0-8]: ").strip()

        if choice == '0':
            break

        elif choice == '1':
            # 导入数据
            
            default_path = "D:/Program/Python/Finance/tools/huatai"
            default_name = "300738_日线.txt"
            path_input = input(f"请输入数据文件夹路径 [默认:{default_path}]: ").strip()
            path = path_input if path_input else default_path
            name_input = input(f"请输入数据文件名 [默认:{default_name}]: ").strip()
            name = name_input if name_input else default_name
            file_path = path + "/" + name

            if not Path(file_path).exists():
                print(f"❌ 文件不存在: {file_path}")
                continue

            analyzer = CandleStickAnalyzer(file_path)
            if analyzer.load_data():
                # 设置窗口标题
                import os
                os.system(f'title {analyzer.stock_name}_{analyzer.chart_type}分析')

                current_file = file_path
            else:
                analyzer = None

        elif choice == '2':
            # 查看最近数据
            if not analyzer:
                print("❌ 请先导入数据!")
                continue
            days = input("查看最近几天? [默认10]: ").strip()
            days = int(days) if days.isdigit() else 10
            analyzer.show_recent_data(days)

        elif choice == '3':
            # 开收关系分析
            if not analyzer:
                print("❌ 请先导入数据!")
                continue
            start = input("开始日期 (YYYY-MM-DD, 直接回车忽略): ").strip()
            end = input("结束日期 (YYYY-MM-DD, 直接回车忽略): ").strip()
            analyzer.analyze_open_close_patterns(start or None, end or None)

        elif choice == '4':
            # 大阳线大阴线分析
            if not analyzer:
                print("❌ 请先导入数据!")
                continue
            threshold_input = input("大阳线/大阴线涨跌幅阈值(%) [默认5.0]: ").strip()
            threshold = float(threshold_input) if threshold_input else 5.0
            start = input("开始日期 (YYYY-MM-DD, 直接回车忽略): ").strip()
            end = input("结束日期 (YYYY-MM-DD, 直接回车忽略): ").strip()
            analyzer.analyze_big_candles(threshold, start or None, end or None)

        elif choice == '5':
            # 成交量分析
            if not analyzer:
                print("❌ 请先导入数据!")
                continue
            start = input("开始日期 (YYYY-MM-DD, 直接回车忽略): ").strip()
            end = input("结束日期 (YYYY-MM-DD, 直接回车忽略): ").strip()
            analyzer.analyze_volume_patterns(start or None, end or None)

        elif choice == '6':
            # 跳空分析
            if not analyzer:
                print("❌ 请先导入数据!")
                continue
            start = input("开始日期 (YYYY-MM-DD, 直接回车忽略): ").strip()
            end = input("结束日期 (YYYY-MM-DD, 直接回车忽略): ").strip()
            analyzer.analyze_gap_patterns(start or None, end or None)

        elif choice == '7':
            # 影线分析
            if not analyzer:
                print("❌ 请先导入数据!")
                continue
            upper_input = input("上影线阈值(倍数) [默认1.0]: ").strip()
            lower_input = input("下影线阈值(倍数) [默认1.0]: ").strip()
            upper_threshold = float(upper_input) if upper_input else 1.0
            lower_threshold = float(lower_input) if lower_input else 1.0
            start = input("开始日期 (YYYY-MM-DD, 直接回车忽略): ").strip()
            end = input("结束日期 (YYYY-MM-DD, 直接回车忽略): ").strip()
            analyzer.analyze_shadow_patterns(upper_threshold, lower_threshold, start or None, end or None)

        elif choice == '8':
            # 综合分析
            if not analyzer:
                print("❌ 请先导入数据!")
                continue
            threshold_input = input("大阳线/大阴线涨跌幅阈值(%) [默认5.0]: ").strip()
            threshold = float(threshold_input) if threshold_input else 5.0
            upper_input = input("上影线阈值(倍数) [默认1.0]: ").strip()
            lower_input = input("下影线阈值(倍数) [默认1.0]: ").strip()
            upper_threshold = float(upper_input) if upper_input else 1.0
            lower_threshold = float(lower_input) if lower_input else 1.0
            start = input("开始日期 (YYYY-MM-DD, 直接回车忽略): ").strip()
            end = input("结束日期 (YYYY-MM-DD, 直接回车忽略): ").strip()
            analyzer.analyze_open_close_patterns(start or None, end or None)
            analyzer.analyze_big_candles(threshold, start or None, end or None)
            analyzer.analyze_volume_patterns(start or None, end or None)
            analyzer.analyze_gap_patterns(start or None, end or None)
            analyzer.analyze_shadow_patterns(upper_threshold, lower_threshold, start or None, end or None)

        else:
            print("❌ 无效选择!")


if __name__ == "__main__":
    main()
