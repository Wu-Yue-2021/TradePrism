"""
K线数据处理工具模块
提供华泰K线数据读取和技术指标计算功能
支持日线、分钟线等多种周期
"""

import pandas as pd
import numpy as np
from typing import Tuple, Optional, Dict, Any


def load_huatai_data(file_path: str) -> Tuple[Optional[pd.DataFrame], Dict[str, Any]]:
    """读取华泰导出的K线数据文件

    Args:
        file_path: 数据文件路径

    Returns:
        (df, meta): df为处理后的DataFrame，meta为元信息字典
        meta包含: stock_code, stock_name, chart_type
        读取失败时返回 (None, {})
    """
    try:
        with open(file_path, 'r', encoding='gbk') as f:
            lines = f.readlines()

        meta: Dict[str, Any] = {
            'stock_code': None,
            'stock_name': None,
            'chart_type': None
        }

        # 解析第一行：300738 奥飞数据 日线 不复权（示例格式）
        if len(lines) > 0:
            first_line = lines[0].strip()
            parts = first_line.split()
            if len(parts) >= 3:
                meta['stock_code'] = parts[0]
                meta['stock_name'] = parts[1]
                meta['chart_type'] = parts[2]

        # 跳过前2行（股票信息、表头）
        data_lines = []
        for line in lines[2:]:
            line = line.strip()
            if line and not line.startswith('数据来源'):
                parts = line.split()
                if len(parts) >= 7:
                    data_lines.append(parts)

        # 解析数据
        records = []
        for parts in data_lines:
            try:
                date_str = parts[0]
                # 支持两种格式：MM/DD/YYYY（日线）或 MM/DD/YYYY HH:MM（分钟线）
                if len(parts) >= 8 and ':' in parts[1]:
                    # 分钟线格式，时间在第二列
                    time_str = parts[1]
                    month, day, year = date_str.split('/')
                    date = f"{year}-{month.zfill(2)}-{day.zfill(2)} {time_str}"
                    value_offset = 1
                else:
                    # 日线格式
                    month, day, year = date_str.split('/')
                    date = f"{year}-{month.zfill(2)}-{day.zfill(2)}"
                    value_offset = 0

                records.append({
                    'date': date,
                    'open': float(parts[1 + value_offset]),
                    'high': float(parts[2 + value_offset]),
                    'low': float(parts[3 + value_offset]),
                    'close': float(parts[4 + value_offset]),
                    'volume': int(parts[5 + value_offset]),
                    'amount': float(parts[6 + value_offset])
                })
            except (ValueError, IndexError):
                continue

        if not records:
            return None, {}

        df = pd.DataFrame(records)
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)

        return df, meta

    except Exception:
        return None, {}


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """计算K线技术指标

    Args:
        df: 包含 date, open, high, low, close, volume, amount 列的DataFrame

    Returns:
        添加了技术指标列的DataFrame
    """
    df = df.copy()

    # K线形态
    df['body'] = df['close'] - df['open']  # 实体
    df['body_abs'] = abs(df['body'])
    df['upper_shadow'] = df['high'] - df[['open', 'close']].max(axis=1)  # 上影线
    df['lower_shadow'] = df[['open', 'close']].min(axis=1) - df['low']  # 下影线
    df['amplitude'] = (df['high'] - df['low']) / df['close'] * 100  # 振幅%

    # 上影线/下影线比例（相对于实体）
    df['upper_shadow_ratio'] = np.where(df['body_abs'] > 0,
                                         df['upper_shadow'] / df['body_abs'],
                                         np.inf)
    df['lower_shadow_ratio'] = np.where(df['body_abs'] > 0,
                                         df['lower_shadow'] / df['body_abs'],
                                         np.inf)

    # 涨跌幅
    df['pct_change'] = df['close'].pct_change() * 100
    df['gap_pct'] = (df['open'] - df['close'].shift(1)) / df['close'].shift(1) * 100  # 跳空

    # 开收关系：低开/高开 vs 前一根K线收盘，高走/低走 vs 当根开盘
    prev_close = df['close'].shift(1)
    conditions = [
        (df['open'] < prev_close) & (df['close'] > df['open']),  # 低开高走
        (df['open'] < prev_close) & (df['close'] <= df['open']),  # 低开低走
        (df['open'] >= prev_close) & (df['close'] > df['open']),  # 高开高走
        (df['open'] >= prev_close) & (df['close'] <= df['open']),  # 高开低走
    ]
    choices = ['低开高走', '低开低走', '高开高走', '高开低走']
    df['open_close_pattern'] = np.select(conditions, choices, default='未知')

    # 成交量相关
    df['volume_diff'] = df['volume'] - df['volume'].shift(1)
    df['volume_ma5'] = df['volume'].rolling(5).mean()
    df['volume_ratio'] = df['volume'] / df['volume_ma5']  # 量比
    df['volume_ratio'] = df['volume_ratio'].fillna(1)

    # 下一根K线涨跌
    df['next_pct_change'] = df['pct_change'].shift(-1)

    return df
