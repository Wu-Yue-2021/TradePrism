"""
分时量能分析工具
分析每根5分钟线相对于当日第一根的量能比例

用法:
    python intraday_volume_analyzer.py <数据文件路径> [天数] [-r]

参数:
    文件路径    华泰分钟线数据文件
    天数        分析天数（默认4天）
    -r          使用相对量能模式（默认绝对量能）

示例:
    python intraday_volume_analyzer.py huatai/603993_5分钟线.txt
    python intraday_volume_analyzer.py huatai/603993_5分钟线.txt 10
    python intraday_volume_analyzer.py huatai/603993_5分钟线.txt 10 -r
"""

import sys
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime

from data_utils import load_huatai_data

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False  # 解决负号显示问题

# 设置UTF-8编码
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


class IntradayVolumeAnalyzer:
    """分时量能分析器"""

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.df: Optional[pd.DataFrame] = None
        self.stock_name: Optional[str] = None
        self.stock_code: Optional[str] = None
        self.analysis_df: Optional[pd.DataFrame] = None

    def load_data(self, deletelast = False) -> bool:
        """加载5分钟线数据"""
        df, meta = load_huatai_data(self.file_path)

        if df is None:
            print("❌ 导入数据失败")
            return False

        self.stock_name = meta.get('stock_name')
        self.stock_code = meta.get('stock_code')
        self.chart_type = meta.get('chart_type')
        import os
        os.system(f'title {self.stock_name}-{self.chart_type}')
        if deletelast:
            df = df[:-1]
        self.df = df
        # 检查是否为分钟线数据
        if meta.get('chart_type') not in ['5分钟线', '1分钟线', '分钟线']:
            print(f"⚠️  当前数据类型为: {meta.get('chart_type')}，建议使用分钟线数据")

        print(f"✅ 成功导入 {len(self.df)} 条数据")
        print(f"📅 日期范围: {self.df['date'].min().date()} 至 {self.df['date'].max().date()}")
        return True

    def analyze(self) -> pd.DataFrame:
        """执行分时量能分析"""
        if self.df is None:
            raise ValueError("请先加载数据")

        df = self.df.copy()

        # 提取日期和时间
        df['trade_date'] = df['date'].dt.date
        df['time'] = df['date'].dt.strftime('%H:%M')

        # 计算每根K线的涨跌幅（收盘价相对于上一根收盘价）
        df['pct_change'] = df['close'].pct_change() * 100

        # 获取每日第一根K线的成交量作为基准
        first_bars = df.groupby('trade_date').first().reset_index()
        first_bars = first_bars[['trade_date', 'volume']]
        first_bars.columns = ['trade_date', 'first_volume']

        # 获取每日最后一根的收盘价
        last_bars = df.groupby('trade_date').last().reset_index()
        last_bars = last_bars[['trade_date', 'close']]
        last_bars.columns = ['trade_date', 'day_close']

        # 合并基准数据
        df = df.merge(first_bars, on='trade_date')
        df = df.merge(last_bars, on='trade_date')

        # 计算当日涨跌幅：当日最后一根收盘价 / 昨日收盘价
        last_bars['prev_close'] = last_bars['day_close'].shift(1)
        last_bars['day_pct_change'] = (last_bars['day_close'] - last_bars['prev_close']) / last_bars['prev_close'] * 100
        day_changes = last_bars[['trade_date', 'day_pct_change']]
        df = df.merge(day_changes, on='trade_date')

        # 计算相对量能比例
        df['volume_ratio'] = df['volume'] / df['first_volume']

        self.analysis_df = df

        return df

    def print_table(self, days: Optional[int] = None):
        """打印量能比例表格"""
        if self.analysis_df is None:
            self.analyze()

        df = self.analysis_df

        if days:
            unique_dates = df['trade_date'].unique()
            recent_dates = sorted(unique_dates)[-days:]
            df = df[df['trade_date'].isin(recent_dates)]

        print("\n" + "="*100)
        print(f"📊 {self.stock_name} 分时量能分析")
        print("="*100)

        # 按日期分组显示
        for date in sorted(df['trade_date'].unique()):
            day_df = df[df['trade_date'] == date]
            day_pct = day_df['day_pct_change'].iloc[0]

            # 涨跌标记
            day_marker = "🟢" if day_pct < 0 else "🔴" if day_pct > 0 else "⚪"

            print(f"\n{day_marker} {date} | 当日涨跌: {day_pct:+.2f}%")
            print("-"*70)
            print(f"{'时间':<6} {'开盘':<6} {'收盘':<6} {'成交量':<9} {'相对量能':<6} {'涨跌幅':<5}")
            print("-"*70)

            for _, row in day_df.iterrows():
                vol_ratio = row['volume_ratio']
                pct = row['pct_change']
                pct_marker = "+" if pct > 0 else ""

                # 量能高亮
                if vol_ratio > 2:
                    vol_str = f"🔥{vol_ratio:.2f}"
                elif vol_ratio > 1:
                    vol_str = f"  {vol_ratio:.2f}"
                else:
                    vol_str = f"  {vol_ratio:.2f}"

                print(f"{row['time']:<8} {row['open']:<8.2f} {row['close']:<8.2f} "
                      f"{row['volume']:<12,} {vol_str:<10} {pct_marker}{pct:.2f}%")

    def plot_charts(self, output_dir: Optional[str] = None, days: Optional[int] = None,
                     use_ratio: bool = False):
        """生成图表"""
        if self.analysis_df is None:
            self.analyze()

        df = self.analysis_df

        if days:
            unique_dates = df['trade_date'].unique()
            recent_dates = sorted(unique_dates)[-days:]
            df = df[df['trade_date'].isin(recent_dates)]

        unique_dates = sorted(df['trade_date'].unique())
        n_days = len(unique_dates)

        if n_days == 0:
            print("❌ 没有数据可绘制")
            return

        # 创建输出目录
        # if output_dir:
        #     output_path = Path(output_dir)
        #     output_path.mkdir(parents=True, exist_ok=True)

        # 创建包含两个子图的窗口
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, max(7, n_days * 0.3)))

        # 图1：第一根量横柱状图
        first_volumes = []
        first_pct_changes = []  # 第一根K线的涨跌幅
        day_changes = []
        date_labels = []

        for date in unique_dates:
            day_df = df[df['trade_date'] == date]
            first_vol = day_df['volume'].iloc[0]
            first_pct = day_df['pct_change'].iloc[0]  # 第一根K线的涨跌幅
            day_pct = day_df['day_pct_change'].iloc[0]

            first_volumes.append(first_vol)
            first_pct_changes.append(first_pct)
            day_changes.append(day_pct)
            date_labels.append(str(date))

        # 颜色根据当日涨跌
        colors = ['#e74c3c' if c > 0 else '#2ecc71' if c < 0 else '#95a5a6' for c in day_changes]

        y_pos = np.arange(n_days)
        bars = ax1.barh(y_pos, first_volumes, color=colors, height=0.6)

        ax1.set_yticks(y_pos)
        ax1.set_yticklabels(date_labels)
        ax1.set_xlabel('前5分钟成交量（颜色表示全天涨跌 小字为前5分钟涨跌幅）')
        ax1.set_title(f'{self.stock_name} 各交易日前5分钟量能')

        # 添加数值标签（第一根K线涨跌幅）
        for i, (bar, vol, pct) in enumerate(zip(bars, first_volumes, first_pct_changes)):
            pct_str = f'{pct:+.2f}%' if not pd.isna(pct) else 'N/A'
            ax1.text(bar.get_width() + max(first_volumes) * 0.01, bar.get_y() + bar.get_height()/2,
                     f'{vol/10000:.0f}万 ({pct_str})', va='center', fontsize=8)

        ax1.invert_yaxis()

        # 图2：相对量能折线图
        # 使用颜色映射为每个日期分配不同颜色
        cmap = plt.colormaps.get_cmap('tab10').resampled(n_days)

        # 获取所有时间点
        all_times = sorted(df['time'].unique())

        # 为每个日期绘制一条折线
        for i, date in enumerate(unique_dates):
            day_df = df[df['trade_date'] == date]
            day_pct = day_df['day_pct_change'].iloc[0]

            # 构建完整的时间序列（填充缺失时间点）
            if use_ratio:
                time_volume_map = dict(zip(day_df['time'], day_df['volume_ratio']))
            else:
                time_volume_map = dict(zip(day_df['time'], day_df['volume']))
            time_pct_map = dict(zip(day_df['time'], day_df['pct_change']))
            volumes = [time_volume_map.get(t, np.nan) for t in all_times]

            color = cmap(i)

            line, = ax2.plot(all_times, volumes, marker='o', markersize=4, linewidth=1.2,
                             color=color, label=f'{date} ({day_pct:+.2f}%)')

            # 在每个点旁边标注涨跌幅
            if i == len(unique_dates) - 1:
                for j, t in enumerate(all_times):
                    if t in time_pct_map and not np.isnan(volumes[j]):
                        pct_val = time_pct_map[t]
                        # 根据涨跌选择颜色
                        text_color = "#f92ba6" if pct_val > 0.05 else "#242bf2" if pct_val < -0.05 else 'gray'
                        ax2.annotate(f'{pct_val:+.1f}', (t, volumes[j]),
                                    textcoords='offset points', xytext=(0, 5),
                                    fontsize=7, color=text_color,
                                    ha='center')    
            else:
                for j, t in enumerate(all_times):
                    if t in time_pct_map and not np.isnan(volumes[j]):
                        pct_val = time_pct_map[t]
                        # 根据涨跌选择颜色
                        text_color = "#e74c3c" if pct_val > 0.05 else "#2ecc71" if pct_val < -0.05 else 'gray'
                        ax2.annotate(f'{pct_val:+.1f}', (t, volumes[j]),
                                    textcoords='offset points', xytext=(0, 5),
                                    fontsize=7, color=text_color,
                                    ha='center')

        ax2.set_xlabel('时间')
        ax2.set_yscale('log')  # 设置对数轴

        # 格式化对数轴刻度
        from matplotlib.ticker import FuncFormatter, LogLocator

        if use_ratio:
            ax2.set_ylabel('相对量能比例（对数）')
            ax2.set_title(f'{self.stock_name} 分时相对量能（以当日前5分钟为基准）')
            ax2.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f'{x:.2f}'))
            ax2.yaxis.set_major_locator(LogLocator(base=10, subs=[0.5, 1, 2, 5])) #在这里设置纵坐标数字
            ax2.axhline(y=1, color='gray', linestyle='--', alpha=0.5, label='基准线(1.0)')
        else:
            ax2.set_ylabel('成交量（对数）')
            ax2.set_title(f'{self.stock_name} 分时量能')
            # 根据数据范围智能选择单位
            all_volumes = df['volume'].values
            max_vol = all_volumes.max()
            if max_vol >= 1e8:  # 亿级
                ax2.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f'{x/1e8:.0f}亿'))
            elif max_vol >= 1e6:  # 百万级
                ax2.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f'{x/1e4:.0f}万'))
            else:  # 小量级
                ax2.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f'{x:.0f}'))
            ax2.yaxis.set_major_locator(LogLocator(base=10, subs=[1, 2, 5])) #在这里设置纵坐标数字
            avg_first_vol = np.mean(first_volumes)
            ax2.axhline(y=avg_first_vol, color='gray', linestyle='--', alpha=0.5,
                        label=f'首根均值({avg_first_vol/10000:.0f}万)')

        # 设置x轴标签（更密集）
        step = max(1, len(all_times) // 20)
        ax2.set_xticks(range(0, len(all_times), step))
        ax2.set_xticklabels([all_times[i] for i in range(0, len(all_times), step)], rotation=45)

        # 图例放在右侧
        ax2.legend(loc='upper left', bbox_to_anchor=(1.02, 1), fontsize=8)

        plt.tight_layout()

        # if output_dir:
        #     fig.savefig(output_path / f'{self.stock_code}_volume_analysis.png', dpi=150,
        #                 bbox_inches='tight')
        #     print(f"✅ 图表已保存: {output_path / f'{self.stock_code}_volume_analysis.png'}")

        plt.show()

    def export_to_excel(self, output_path: str):
        """导出分析结果到Excel"""
        if self.analysis_df is None:
            self.analyze()

        df = self.analysis_df.copy()

        # 选择输出列
        output_df = df[['trade_date', 'time', 'open', 'close', 'high', 'low',
                        'volume', 'amount', 'volume_ratio', 'pct_change',
                        'day_pct_change']].copy()

        output_df.columns = ['日期', '时间', '开盘', '收盘', '最高', '最低',
                             '成交量', '成交额', '相对量能', '涨跌幅%', '当日涨跌%']

        output_df.to_excel(output_path, index=False)
        print(f"✅ 已导出到: {output_path}")


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='分时量能分析工具')
    parser.add_argument('file_path', help='数据文件路径') # 参数名前没有-或--，是位置参数（顺序敏感）
    parser.add_argument('days', nargs='?', type=int, default=4, help='分析天数（默认4天）') # nargs='?'表示参数可选，使用默认值
    parser.add_argument('-r', '--ratio', action='store_true', help='使用相对量能模式') # 使用store_true表示参数为布尔值，默认为False，只要命令行中出现这个参数就为True
    parser.add_argument('-l', '--deletelast', action='store_true', help='删除最后一行数据')
    args = parser.parse_args()

    if not Path(args.file_path).exists():
        print(f"❌ 文件不存在: {args.file_path}")
        return

    analyzer = IntradayVolumeAnalyzer(args.file_path)

    if not analyzer.load_data(args.deletelast):
        return

    # 执行分析
    analyzer.analyze()

    # 打印表格
    analyzer.print_table(args.days)
    _ = input("回车键生成图表...")
    # 生成图表
    output_dir = Path(args.file_path).parent / 'export'
    analyzer.plot_charts(str(output_dir), args.days, use_ratio=args.ratio)


if __name__ == "__main__":
    main()