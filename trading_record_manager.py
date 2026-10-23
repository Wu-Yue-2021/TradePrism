"""
交易记录管理工具
支持交易记录的保存、查询、修改和导出
"""
import json
import os
import shutil
from datetime import datetime, timedelta
import unicodedata
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path

# ===============================
# 配置
# ===============================
DATA_DIR = Path(__file__).parent / "data"  # 创建专门的数据目录
if not os.path.exists(DATA_DIR):
    os.mkdir(DATA_DIR)
EXPORT_DIR = Path(__file__).parent / "data" / "export" # 创建导出目录
if not os.path.exists(EXPORT_DIR):
    os.mkdir(EXPORT_DIR)
FILE = DATA_DIR / "trades.json"
BACKUP_INTERVAL_DAYS = 7
PRACTICE = True  # 是否具有实际意义（否则不会按100取整）
CASH_SYMBOL = "【现金】"  # 现金品种名称
CASH_PRICE = 0.01    # 现金固定价格

# ANSI 颜色常量
BLUE = '\033[94m'
RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
GREY = '\033[90m'
RESET = '\033[0m'

# 不高亮版本（颜色层次用，做T交易使用）
DARK_RED = '\033[31m'
DARK_BLUE = '\033[34m'
DARK_YELLOW = '\033[33m'
DARK_CYAN = '\033[36m'
DARK_COLOR = {RED: DARK_RED, BLUE: DARK_BLUE, YELLOW: DARK_YELLOW, CYAN: DARK_CYAN}

def ensure_weekly_backup(force: bool = False) -> None:
    if not FILE.exists():
        return

    today = datetime.now().date()
    backup_dir = None
    backup_date = None

    for path in DATA_DIR.iterdir():
        if not path.is_dir() or not path.name.startswith("backup_"):
            continue
        try:
            current_backup_date = datetime.strptime(path.name[7:], "%Y-%m-%d").date()
        except ValueError:
            continue
        if backup_date is None or current_backup_date > backup_date: #取最大值
            backup_dir = path
            backup_date = current_backup_date

    if not force and backup_date and (today - backup_date).days < BACKUP_INTERVAL_DAYS:
        return

    target_dir = DATA_DIR / f"backup_{today.isoformat()}"
    if backup_dir is None:
        backup_dir = target_dir
        backup_dir.mkdir(exist_ok=True)
    elif backup_dir != target_dir:
        if not target_dir.exists():
            backup_dir.rename(target_dir)
        backup_dir = target_dir
        backup_dir.mkdir(exist_ok=True)

    backup_file = backup_dir / f"{FILE.stem}_{today.isoformat()}{FILE.suffix}"
    shutil.copy2(FILE, backup_file)
    print(f"{YELLOW}交易记录备份成功{RESET}")


# ===============================
# 数据管理：读写 + 增删改查
# ===============================
class DataManager:
    """数据管理类：负责数据读写和记录的增删改查"""

    @staticmethod
    def load_trades() -> List[Dict[str, Any]]:
        """加载交易记录"""
        if not os.path.exists(FILE):
            return []
        with open(FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def save_trades(data: Optional[List[Dict[str, Any]]]) -> None:
        """保存交易记录"""
        with open(FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @staticmethod
    def get_symbol_block(symbol: str) -> Optional[Dict[str, Any]]:
        """获取指定品种的数据块"""
        data = DataManager.load_trades()
        for block in data:
            if block["symbol"] == symbol:
                return block
        return None

    @staticmethod
    def get_last_price(symbol: str) -> float:
        """获取品种最后一次交易的价格"""
        block = DataManager.get_symbol_block(symbol)
        if block and block["records"]:
            return float(block["records"][-1]["price"])
        return 0.0

    @staticmethod
    def get_current_state(symbol: str, time: Optional[str] = None) -> tuple[int, float]:
        """
        获取品种当前状态或时间小于等于time的最后一条状态
        返回: (当前份额, 当前均价)
        """
        block = DataManager.get_symbol_block(symbol)
        if not block or not block["records"]:
            return 0, 0.0

        # 无时间参数时直接返回最后一条记录
        if time is None:
            last = block["records"][-1]
            return int(last["current_qty"]), float(last["avg_cost_after"])

        # 有时间参数时遍历查找
        for record in reversed(block["records"]):
            if record["ts"] <= time: # 时间大于time就跳过
                return int(record["current_qty"]), float(record["avg_cost_after"])

        return 0, 0.0

    @staticmethod
    def add_trade(symbol: str, record: Dict[str, Any]) -> None:
        """新增一条交易记录"""
        data = DataManager.load_trades()

        for block in data:
            if block["symbol"] == symbol:
                block["records"].append(record)
                block["records"].sort(key=lambda r: r["ts"])
                break
        else:
            # 新品种，追加到末尾
            data.append({
                "symbol": symbol,
                "records": [record]
            })

        DataManager.save_trades(data)

    @staticmethod
    def update_trade(symbol: str, index: int, **updates) -> bool:
        """修改某品种第 index 条记录，返回是否修改成功"""
        data = DataManager.load_trades()

        for block in data:
            if block["symbol"] == symbol:
                if 0 <= index < len(block["records"]):
                    block["records"][index].update(updates)
                    block["records"].sort(key=lambda r: r["ts"])
                    DataManager.save_trades(data)
                    return True
                return False

        return False

    @staticmethod
    def delete_trade(symbol: str, index: int) -> bool:
        """删除某品种第 index 条记录，返回是否删除成功"""
        data = DataManager.load_trades()
        cash_block = next((b for b in data if b["symbol"] == CASH_SYMBOL), None)

        for block in data:
            if block["symbol"] == symbol:
                if 0 <= index < len(block["records"]):
                    record = block["records"][index]
                    # 同步删除现金记录（用价格×数量近似匹配，允许误差10元）；传入 data 避免重复读写
                    if record["side"] in ("BUY", "SELL", "NEW") and cash_block:
                        occur_amount_approx = float(record["price"]) * int(record["delta_qty"])
                        DataManager.delete_cash_record(record["ts"], -occur_amount_approx, tolerance=10.0, cash_block=cash_block)
                    block["records"].pop(index)
                    DataManager.save_trades(data)
                    return True
                return False

        return False

    @staticmethod
    def delete_trades_batch(symbol: str, indices: List[int]) -> int:
        """
        批量删除某品种的多条记录
        symbol: 品种代码
        indices: 要删除的索引列表（从0开始）
        返回: 成功删除的记录数
        """
        if not indices:
            return 0

        data = DataManager.load_trades()
        cash_block = next((b for b in data if b["symbol"] == CASH_SYMBOL), None)

        for block in data:
            if block["symbol"] == symbol:
                # 按索引降序排序，从后往前删除避免索引错位
                sorted_indices = sorted(set(indices), reverse=True)
                deleted_count = 0

                for idx in sorted_indices:
                    if 0 <= idx < len(block["records"]):
                        record = block["records"][idx]
                        # 同步删除现金记录（传入 data 避免重复读写）
                        if record["side"] in ("BUY", "SELL", "NEW") and cash_block:
                            occur_amount_approx = float(record["price"]) * int(record["delta_qty"])
                            DataManager.delete_cash_record(record["ts"], -occur_amount_approx, tolerance=10.0, cash_block=cash_block)
                        block["records"].pop(idx)
                        deleted_count += 1

                if deleted_count > 0:
                    DataManager.save_trades(data)
                return deleted_count

        return 0

    @staticmethod
    def delete_symbol(symbol: str) -> bool:
        """删除整个品种及其所有记录，返回是否删除成功"""
        data = DataManager.load_trades()

        for i, block in enumerate(data):
            if block["symbol"] == symbol:
                data.pop(i)
                DataManager.save_trades(data)
                return True

        return False

    @staticmethod
    def rename_symbol(old_name: str, new_name: str) -> bool:
        """修改品种名称，返回是否修改成功"""
        if not new_name or not new_name.strip():
            return False

        data = DataManager.load_trades()
        new_name = new_name.strip().upper()

        # 检查新名称是否已存在
        for block in data:
            if block["symbol"] == new_name and block["symbol"] != old_name:
                print(f"品种【{new_name}】已存在，无法重命名")
                return False

        # 修改名称
        for block in data:
            if block["symbol"] == old_name:
                block["symbol"] = new_name
                DataManager.save_trades(data)
                return True

        return False

    @staticmethod
    def filter_trades(
        symbols: Optional[set[str]] = None,
        sides: Optional[set[str]] = None,
        start_ts: Optional[str] = None,
        end_ts: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """筛选交易记录"""
        data = DataManager.load_trades()
        result = []

        for block in data:
            if symbols and block["symbol"] not in symbols:
                continue

            for r in block["records"]:
                if sides and r["side"] not in sides:
                    continue
                if start_ts and r["ts"] < start_ts:
                    continue
                if end_ts and r["ts"] > end_ts:
                    continue

                result.append({
                    "symbol": block["symbol"],
                    **r
                })

        return result

    @staticmethod
    def list_symbols() -> tuple[List[str], List[float]]:
        """列出所有品种名称、当前总额、按总额倒序返回"""
        data = DataManager.load_trades()
        try:
            data.sort(key=lambda b: int(b["records"][-1]["current_qty"]) * float(b["records"][-1]["price"]), reverse=True)

            return [block["symbol"] for block in data], [int(block["records"][-1]["current_qty"]) * float(block["records"][-1]["price"]) for block in data]
        except:
            print("错误：不能有记录为空的品种，请至data/trade.json文件手动修改")
            return [], []
    @staticmethod
    def cascade_update_cash_records(cash_block: Dict[str, Any], from_index: int) -> None:
        """
        级联更新现金记录（从指定索引开始重新计算 current_qty）
        cash_block: 现金品种的数据块
        from_index: 开始更新的索引
        """
        records = cash_block["records"]
        if from_index <= 0:
            prev_qty = 0
        else:
            prev_qty = int(records[from_index - 1]["current_qty"])

        for i in range(from_index, len(records)):
            if records[i]["side"] == "RESET":
                prev_qty = int(records[i]["current_qty"])
                break
            delta_qty = int(records[i]["delta_qty"])
            records[i]["current_qty"] = prev_qty + delta_qty
            prev_qty = records[i]["current_qty"]

    @staticmethod
    def add_cash_record(ts: str, occur_amount: float, cash_block: Optional[Dict[str, Any]] = None) -> None:
        """
        添加现金记录
        ts: 时间戳
        occur_amount: 发生金额（正数=现金增加=BUY，负数=现金减少=SELL）
        cash_block: 可选的现金品种数据块，若提供则直接操作，否则重新加载
        """
        all_data = None # 相当于起了save_after的作用，如果有传入数据，则修改引用即可，传入数据的调用者负责保存
        if cash_block is None: # 获取现金品种；判断是否传入数据，区分批量调用还是独立调用（需要写入内存），优化批量操作的性能
            all_data = DataManager.load_trades()
            cash_block = next((b for b in all_data if b["symbol"] == CASH_SYMBOL), None)
            if cash_block is None:
                cash_block = {"symbol": CASH_SYMBOL, "records": []}
                all_data.append(cash_block)

        # 确定现金操作方向：发生金额>0 表示现金增加(买入现金/卖出股票收入)，发生金额<0 表示现金减少
        if occur_amount > 0:
            cash_side = "BUY"
            cash_delta_qty = int(occur_amount * 100)  # 份额 = 100 × 金额
        else:
            cash_side = "SELL"
            cash_delta_qty = int(occur_amount * 100)  # 负数

        # 找到该时间点之前的状态
        old_cash_qty = 0
        for rec in reversed(cash_block["records"]):
            if rec["ts"] < ts:
                old_cash_qty = int(rec["current_qty"])
                break

        new_cash_qty = old_cash_qty + cash_delta_qty
        # 现金均价始终为 0.01
        cash_record = {
            "ts": ts,
            "side": cash_side,
            "price": f"{CASH_PRICE:.3f}",
            "current_qty": new_cash_qty,
            "delta_qty": cash_delta_qty,
            "avg_cost_after": f"{CASH_PRICE:.4f}"
        }
        cash_block["records"].append(cash_record)
        cash_block["records"].sort(key=lambda r: r["ts"])

        # 级联更新：找到新记录的位置，如果找到且不是最后一条则更新后续记录，找不到则是-1
        new_record_index = next((i for i, r in enumerate(cash_block["records"]) if r["ts"] == ts), -1)
        if new_record_index != -1 and new_record_index < len(cash_block["records"]) - 1:
            DataManager.cascade_update_cash_records(cash_block, new_record_index)

        if all_data is not None:
            DataManager.save_trades(all_data)

    @staticmethod
    def delete_cash_record(ts: str, occur_amount: float, tolerance: float = 1.0, cash_block: Optional[Dict[str, Any]] = None) -> bool:
        """
        删除现金记录（按时间、金额定位，允许误差）
        ts: 时间戳
        occur_amount: 发生金额
        tolerance: 金额误差容忍度（默认1.0元）
        cash_block: 可选的现金品种数据块，若提供则直接操作，否则重新加载
        返回: 是否删除成功
        """
        all_data = None
        if cash_block is None:
            all_data = DataManager.load_trades()
            cash_block = next((b for b in all_data if b["symbol"] == CASH_SYMBOL), None)
            if not cash_block:
                return False

        # 计算对应的份额
        cash_delta_qty = int(occur_amount * 100)

        # 查找匹配的记录（时间精确匹配，份额允许误差）
        for i, rec in enumerate(cash_block["records"]):
            if rec["ts"] == ts:
                rec_delta_qty = int(rec["delta_qty"])
                # 份额误差容忍（对应金额误差）
                if abs(rec_delta_qty - cash_delta_qty) <= int(tolerance * 100):
                    cash_block["records"].pop(i)
                    # 级联更新：如果不是删除最后一条，则更新后续记录
                    if i < len(cash_block["records"]):
                        DataManager.cascade_update_cash_records(cash_block, i)
                    if all_data is not None:
                        DataManager.save_trades(all_data)
                    return True
        return False

# 向后兼容的别名
load_trades = DataManager.load_trades
save_trades = DataManager.save_trades
get_symbol_block = DataManager.get_symbol_block
get_last_price = DataManager.get_last_price
get_current_state = DataManager.get_current_state
add_trade = DataManager.add_trade
update_trade = DataManager.update_trade
delete_trade = DataManager.delete_trade
delete_symbol = DataManager.delete_symbol
rename_symbol = DataManager.rename_symbol
filter_trades = DataManager.filter_trades
list_symbols = DataManager.list_symbols


# ===============================
# 计算功能
# ===============================
def calculate_new_state(current_qty: int, avg_cost: float,
                       change_qty: int, cur_price: float) -> tuple[float, float, float]:
    """
    计算变动后的状态
    返回: (new_avg_cost, current_volume, new_volume)
    """
    invest = current_qty * avg_cost  # 总投入
    new_invest = invest + change_qty * cur_price
    current_volume = current_qty * cur_price
    new_volume = current_volume + change_qty * cur_price

    if current_qty + change_qty == 0:
        new_avg_cost = 0  # 原为float("inf")
    else:
        new_avg_cost = new_invest / (current_qty + change_qty)

    return new_avg_cost, current_volume, new_volume


def generate_date_range(start: datetime, end: datetime) -> list[str]:
    """生成日期列表（包含首尾），格式 YYYY-MM-DD"""
    dates = []
    current = start
    while current <= end:
        dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return dates


def analyze_position_changes(start_date: datetime, end_date: datetime) -> None:
    """
    分析持仓仓位历史变化
    每日输出：总市值、股票市值、现金、仓位比例
    变化品种：显示仓位变动和占比变化
    """
    from irr_analyzer import TradeAnalyzer

    data = DataManager.load_trades()
    dates = generate_date_range(start_date, end_date)

    if not dates:
        print("日期范围无效")
        return

    # 获取所有品种（包括现金）
    symbol_blocks = [(b["symbol"], b["records"]) for b in data if b["records"]]

    if not symbol_blocks:
        print("无交易记录")
        return

    daily_total = {}        # {date: total_value}
    daily_stock = {}        # {date: stock_value}
    daily_cash = {}         # {date: cash_value}
    daily_positions = {}    # {date: {symbol: {"value": value, "qty": qty, "price": price}}}
    daily_sec_inflow = {}   # {date: Σ买入(含费) − Σ卖出(扣费)}，识别出入金用

    # === 每个品种遍历一次，指针推进 ===
    for symbol, records in symbol_blocks:
        rec_idx = 0
        curr_qty, curr_price = 0, 0.0

        for date in dates:
            time_point = f"{date} 23:59:59"

            # 推进指针到该日期前最后一条记录
            while rec_idx < len(records) and records[rec_idx]["ts"] <= time_point:
                rec = records[rec_idx]
                curr_qty = int(rec["current_qty"])
                curr_price = float(rec["price"])
                # 顺带累计证券侧净流入：买入使现金流出(含费)、卖出使现金流入(扣费)。
                # 口径同 PortfolioAnalyzer._security_net_inflow_by_day，改动需同步
                if symbol != CASH_SYMBOL and rec["side"] in ("BUY", "NEW", "SELL"):
                    qty = abs(int(rec["delta_qty"]))
                    amount = float(rec["price"]) * qty
                    fee = TradeAnalyzer.calculate_trade_fee(
                        float(rec["price"]), qty, rec["side"], symbol,
                        datetime.strptime(rec["ts"], "%Y-%m-%d %H:%M:%S"))
                    day = rec["ts"][:10]
                    flow = amount + fee if rec["side"] in ("BUY", "NEW") else -(amount - fee)
                    daily_sec_inflow[day] = daily_sec_inflow.get(day, 0.0) + flow
                rec_idx += 1

            value = curr_qty * curr_price
            daily_positions.setdefault(date, {})[symbol] = {"value": value, "qty": curr_qty, "price": curr_price}

            if symbol == CASH_SYMBOL:
                daily_cash[date] = daily_cash.get(date, 0) + value
            else:
                daily_stock[date] = daily_stock.get(date, 0) + value
            daily_total[date] = daily_total.get(date, 0) + value

    # === 输出：对比相邻日期，只显示有持仓变化或出入金的日期 ===
    symbols = [s for s, _ in symbol_blocks]

    for i, date in enumerate(dates):
        total = daily_total.get(date, 0)
        stock = daily_stock.get(date, 0)
        cash = daily_cash.get(date, 0)
        ratio = (stock / total * 100) if total > 0 else 0

        # 首日只记录数据，不输出
        if i == 0:
            continue

        prev_date = dates[i - 1]
        prev_total = daily_total.get(prev_date, 0)

        # 外部资金（出入金）= Δ现金 + 证券侧净流入：买卖证券造成的现金变化被第二项抵消，
        # 余下的即无对应证券交易的资金进出。不足 1 元的残差是费率估算舍入，忽略
        external = (cash - daily_cash.get(prev_date, 0)) + daily_sec_inflow.get(date, 0.0)
        if abs(external) < 20:
            external = 0.0

        # 收集有持仓数量变化的品种
        changed_symbols = []
        for symbol in symbols:
            if symbol == CASH_SYMBOL:
                continue

            curr_data = daily_positions.get(date, {}).get(symbol, {"value": 0, "qty": 0})
            prev_data = daily_positions.get(prev_date, {}).get(symbol, {"value": 0, "qty": 0})

            curr_qty = curr_data["qty"]
            prev_qty = prev_data["qty"]

            if curr_qty != prev_qty:  # 持仓数量有变化
                curr_val = curr_data["value"]
                prev_val = prev_data["value"]
                qty_change = curr_qty - prev_qty
                curr_price = curr_data.get("price", 0)
                delta = qty_change * curr_price
                curr_ratio = curr_val / total * 100 if total > 0 else 0
                prev_ratio = prev_val / prev_total * 100 if prev_total > 0 else 0

                changed_symbols.append({
                    "symbol": symbol,
                    "delta": delta,
                    "curr_ratio": curr_ratio,
                    "prev_ratio": prev_ratio,
                    "curr_qty": curr_qty,
                    "prev_qty": prev_qty
                })

        # 没有持仓变化则跳过该日期（出入金本身也是值得单独列出的一天）
        if not changed_symbols and not external:
            continue
        changed_symbols.sort(key=lambda s: abs(float(s["delta"])), reverse= True)
        # 颜色：总市值增加红色，减少绿色
        total_color = RED if total > prev_total else (GREEN if total < prev_total else RESET)

        print(f"\n{GREY}{date}{RESET}  总资产: {total_color}{total:.0f}{RESET}  股票: {stock:.0f}  现金: {cash:.0f}  仓位: {ratio:.1f}%")

        if external:
            print(f"  {CYAN}→ 外部资金: {external:+.0f}{RESET}")

        # 输出变化的品种
        for item in changed_symbols:
            symbol = item["symbol"]
            delta = item["delta"]
            curr_ratio = item["curr_ratio"]
            prev_ratio = item["prev_ratio"]
            curr_qty = item["curr_qty"]
            prev_qty = item["prev_qty"]

            # 颜色：加仓红色，减仓绿色
            action_color = RED if delta > 0 else BLUE

            # 标记新增/清仓
            marker = ""
            if prev_qty == 0 and curr_qty > 0:
                marker = "[开仓]"
            elif prev_qty > 0 and curr_qty == 0:
                marker = "[清仓]"

            print(f"  → {symbol}: {action_color}{delta:+.0f}{RESET} [{prev_qty:.0f}→{curr_qty:.0f}] ({prev_ratio:.1f}% → {curr_ratio:.1f}%){CYAN}{marker}{RESET}")

    print(f"\n注意：\n变动金额基于当日最后价格估算，实际值请以交易记录为准；由于并非所有标的每日更新，股票和仓位仅供参考。")
    print("外部资金 = 当日现金变化 − 当日证券侧现金流出，即无对应交易的出入金（实测值），可能来源为银证转账或股息入账。")
    print(f"回车以继续...")
    input()


def position_history_interactive() -> None:
    """持仓历史分析交互界面"""
    print("\n--- 持仓仓位历史分析 ---")

    # 默认最近30天
    end_date = datetime.now()
    start_date = end_date - timedelta(days=30)

    print(f"默认分析最近30天: {start_date.date()} 至 {end_date.date()}")
    print("进入高级模式选择日期范围？[Y]: ", end="")

    if input().strip().upper() == "Y":
        start_input = input(f"起始日期 (YYYY-MM-DD) [默认 {start_date.date()}]: ").strip()
        end_input = input(f"结束日期 (YYYY-MM-DD) [默认 {end_date.date()}]: ").strip()

        if start_input:
            try:
                start_date = datetime.strptime(start_input, "%Y-%m-%d")
            except ValueError:
                print(f"日期格式错误，使用默认 {start_date.date()}")

        if end_input:
            try:
                end_date = datetime.strptime(end_input, "%Y-%m-%d")
            except ValueError:
                print(f"日期格式错误，使用默认 {end_date.date()}")

    if start_date > end_date:
        start_date, end_date = end_date, start_date
    try:
        import ctypes
        import sys
        if sys.platform == "win32":
            ctypes.windll.kernel32.SetConsoleTitleW(f"持仓变化@{start_date.month}.{start_date.day}-{end_date.month}.{end_date.day}")
    except:
        pass
    analyze_position_changes(start_date, end_date)


# ===============================
# IRR 分析功能
# ===============================
def analyze_symbol_irr(
    symbol: str,
    current_price: float,
    start_ts: Optional[str] = None,
    end_ts: Optional[str] = None,
    window_days: int = 3,
    exclude_t: bool = True
) -> None:
    """
    分析指定品种的 IRR
    symbol: 品种代码
    current_price: 当前价格
    start_ts: 起始时间（留空表示不限）
    end_ts: 结束时间（留空表示不限）
    window_days: 做T判定窗口（天数），默认3天
    """
    from irr_analyzer import TradeAnalyzer, preprocess_records, analyze_and_print_irr

    block = get_symbol_block(symbol)
    if not block or not block["records"]:
        print(f"品种 {symbol} 暂无交易记录")
        return

    # 预处理（时间筛选 + 过滤 RESET/RECORD），run() 内部自行解析 ts/price/delta_qty 并转化数据类型（目前部分是str）
    processed_records = preprocess_records(block["records"], start_ts, end_ts)

    # 创建分析器并运行
    analyzer = TradeAnalyzer(processed_records, current_price, symbol, end_ts, window_days)
    analyzer.run()

    # 打印报告
    analyze_and_print_irr(analyzer,exclude_t=exclude_t)


def irr_analysis_interactive(symbol: str, default_price: float) -> None:
    """
    IRR 分析交互界面
    symbol: 品种代码
    default_price: 默认期末价格
    """
    print("\n--- 投资回报率(ROI)与内部收益率(IRR)分析 ---")
    # 默认参数
    analysis_price, start_ts, end_ts, window_days, exclude_t = default_price, None, None, 3, True
    # 高级模式（自定义参数）
    print("是否进入高级模式（自定义参数）？[Y]: ", end="")
    advanced_mode = input().strip().upper()
    if advanced_mode == "Y":
        # 时间范围筛选
        print("请输入时间(如(2026-)01-25 HH:MM(:SS)，留空表示不限；\n日期可替换成t(oday)，仅日期会补充默认时间，仅时间则日期为今天): ")
        start_ts = get_input_time("起始时间 (默认：00:00:00): ", "00:00:00")
        end_ts = get_input_time("结束时间 (默认：23:59:59): ", "23:59:59")
        # 获取期末价格
        analysis_price = input_with_default("期末价格", analysis_price)
        # 获取做T窗口时间
        window_days = int(input_with_default("做T窗口时间(天)", window_days))
        
        exclude_t = False if input("计算 IRR 时是否包含做T交易？[Y]: ").strip().upper() == "Y" else True

    # 执行分析并输出
    analyze_symbol_irr(symbol, analysis_price, start_ts, end_ts, window_days, exclude_t)
    input("回车以继续...")


def portfolio_review_interactive() -> None:
    """组合复盘交互界面：跨品种的区间资金效率报告"""
    from portfolio_analyzer import PortfolioAnalyzer
    from portfolio_review import print_portfolio_review

    print("\n--- 组合复盘（跨品种区间分析）---")

    today = datetime.now()
    lastweekday = today - timedelta(days=3) if today.weekday() == 0 else today - timedelta(days=1)
    this_month_start = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_month_end = this_month_start - timedelta(seconds=1)
    prev_month_start = prev_month_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    print(f"  1. 上月 ({prev_month_start:%Y-%m-%d} ~ {prev_month_end:%Y-%m-%d})")
    print(f"  2. 本月至今 ({this_month_start:%Y-%m-%d} ~ {today:%Y-%m-%d})")
    print(f"  3. 上个交易日 ({lastweekday:%Y-%m-%d})")
    print("  4. 自定义")
    sel = input("选择统计区间 [1]: ").strip() or "1"

    if sel == "2":
        start_ts, end_ts = this_month_start.strftime("%Y-%m-%d 00:00:00"), today.strftime("%Y-%m-%d 23:59:59")
    elif sel == "3":
        start_ts, end_ts = lastweekday.strftime("%Y-%m-%d 00:00:00"), lastweekday.strftime("%Y-%m-%d 23:59:59")
    elif sel == "4":
        print("请输入起止时间（如(2026-)01-25 HH:MM(:SS)，留空表示不限；\n日期可替换成t(oday)，仅日期会补充默认时间，仅时间则日期为今天): ")
        start_ts = get_input_time("起始时间 (默认：00:00:00): ", "00:00:00")
        end_ts = get_input_time("结束时间 (默认：23:59:59): ", "23:59:59")
        if not start_ts or not end_ts:
            print("自定义区间需要同时给出起止时间")
            input("回车以继续...")
            return
    else:
        start_ts, end_ts = prev_month_start.strftime("%Y-%m-%d 00:00:00"), prev_month_end.strftime("%Y-%m-%d 23:59:59")

    window_days = int(input_with_default("做T窗口时间(天)", 3))

    blocks = DataManager.load_trades()
    if not blocks:
        print("暂无品种记录")
        input("回车以继续...")
        return
    try:
        import ctypes
        import sys
        if sys.platform == "win32":
            ctypes.windll.kernel32.SetConsoleTitleW(f"交易复盘@{start_ts[5:10]}".replace("-", ".")+"-"+f"{end_ts[5:10]}".replace("-", "."))
    except:
        pass
    
    records_by_symbol = {b["symbol"]: b["records"] for b in blocks}
    # 价格体检：只对需要人工确认的品种提示（区间内无记录，或期初价过于陈旧）
    LAG_WARN_DAYS = 30.0
    pa = PortfolioAnalyzer(records_by_symbol, start_ts, end_ts, window_days,
                           cash_symbol=CASH_SYMBOL, cash_scale=1 / CASH_PRICE)
    need = [r for r in pa.scan_prices()
            if (not r['has_interval_record'] and r['qty0'] > 0)
            or (r['open_lag_days'] or 0) > LAG_WARN_DAYS]

    overrides: Dict[str, Dict[str, float]] = {}
    if need:
        print(f"\n价格体检：{len(need)} 个标的的估值价格需要确认")
        for i, r in enumerate(need, 1):
            reason = ("区间内无记录 ⇒ 期初价与期末价同源，区间浮动只能按 0 计"
                      if not r['has_interval_record'] else
                      f"期初价取自 {r['open_date'][:10]}，距区间起点 {r['open_lag_days']:.0f} 天")
            print(f"  {i:>2}. {pad_string(r['symbol'], 14)}{reason}")
        print("  输入序号可指定该标的的期初/期末价（可多次；改后自动补录价格记录，回车开始分析）")
        while True:
            sel = input("序号: ").strip()
            if not sel:
                break
            if not sel.isdigit() or not (1 <= int(sel) <= len(need)):
                print("  无效序号")
                continue
            r = need[int(sel) - 1]
            print(f"  【{r['symbol']}】期初持仓 {r['qty0']} 股，当前推断 期初 "
                  f"{(r['open_price'] or 0):.3f} / 期末 {(r['end_price'] or 0):.3f}")
            op = input_with_default("    期初价", r['open_price'] or 0.0)
            ep = input_with_default("    期末价", r['end_price'] or 0.0)
            overrides[r['symbol']] = {'open': op, 'end': ep}
            record_price_override(r['symbol'], start_ts, op)
            record_price_override(r['symbol'], end_ts, ep)
            print(f"  已补录价格记录：期初 {op:.3f} @ {start_ts[:10]}，期末 {ep:.3f} @ {end_ts[:10]}")

    if overrides:
        pa = PortfolioAnalyzer(records_by_symbol, start_ts, end_ts, window_days, overrides,
                               cash_symbol=CASH_SYMBOL, cash_scale=1 / CASH_PRICE)
    print_portfolio_review(pa.run())
    input("回车以继续...")


def record_price_override(symbol: str, ts: str, price: float) -> None:
    """为复盘补录一条价格记录（RECORD），下次分析同一区间可直接取用

    与「仅记录价格」菜单写出的记录同构：delta_qty=0，持仓与均价取该时点的既有状态。
    期初价录在区间起点、期末价录在区间末——resolve_price 取 <= 该时点，故下次能命中；
    而 state_before 取严格 <，期初持仓的判定不受这条记录影响。
    """
    qty, avg_cost = DataManager.get_current_state(symbol, ts)
    DataManager.add_trade(symbol, {
        "ts": ts,
        "side": "RECORD",
        "price": f"{price:.3f}",
        "current_qty": qty,
        "delta_qty": 0,
        "avg_cost_after": f"{avg_cost:.4f}",
    })


# ===============================
# 文件处理：导出 + 导入
# ===============================
class FileHandler:
    """文件处理类：负责导入导出功能"""

    @staticmethod
    def export_to_csv(data: List[Dict[str, Any]], output_file: str = "trades_export.csv") -> None:
        """导出交易记录到 CSV 文件"""
        if not data:
            print("没有记录可导出")
            return
        with open(EXPORT_DIR / output_file, "w", encoding="utf-8-sig") as f:
            f.write("品种,时间,操作,价格,变动份额,变动后份额,变动后均价\n")
            for record in data:
                f.write(f"{record['symbol']},{record['ts']},{record['side']},{record['price']},{record['delta_qty']},{record['current_qty']},{record['avg_cost_after']}\n")
        print(f"已导出 {len(data)} 条记录到 {EXPORT_DIR / output_file}")

    @staticmethod
    def export_to_json(data: List[Dict[str, Any]], output_file: str = "trades_export.json") -> None:
        """导出交易记录到 JSON 文件"""
        if not data:
            print("没有记录可导出")
            return
        with open(EXPORT_DIR / output_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"已导出 {len(data)} 条记录到 {EXPORT_DIR / output_file}")

    @staticmethod
    def parse_statement_text(text: str) -> List[Dict[str, Any]]:
        """解析交割单文本，提取交易记录"""
        records = []
        lines = text.strip().split('\n')
        header_indices = {}
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if "成交日期" in line and "证券代码" in line:
                parts = line.replace('\t', ' ').split()
                for idx, part in enumerate(parts):
                    if "成交日期" in part: header_indices["date"] = idx
                    elif "成交时间" in part: header_indices["time"] = idx
                    elif "证券名称" in part: header_indices["symbol_name"] = idx
                    # elif "业务名称" in part: header_indices["business"] = idx # 有些版本的同花顺没有这一项，所以注释掉，以操作为准
                    elif "操作" in part: header_indices["operation"] = idx
                    elif "成交数量" in part: header_indices["quantity"] = idx
                    elif "成交均价" in part: header_indices["price"] = idx
                    elif "发生金额" in part: header_indices["occur_amount"] = idx
                break
        if not header_indices:
            return []
        for line in lines:
            line = line.strip()
            if not line or any(keyword in line for keyword in ["成交日期", "证券代码", "业务名称", "发生金额"]):
                continue
            parts = line.replace('\t', ' ').split()
            required_fields = ["date", "time", "symbol_name", "operation", "quantity", "price", "occur_amount"]
            max_idx = max(header_indices.get(f, 0) for f in required_fields)
            if len(parts) <= max_idx:
                continue
            try:
                date_str = parts[header_indices["date"]]
                time_str = parts[header_indices["time"]]
                symbol_name = parts[header_indices["symbol_name"]]
                if 'ETF' in symbol_name:
                    symbol_name = symbol_name.split('ETF')[0].strip()
                    symbol_name = symbol_name.replace("创业板", "创")
                    if len(symbol_name)<4:symbol_name = symbol_name+'ETF'
                if 'GC' in symbol_name or 'R' in symbol_name:
                # if symbol_name == 'GC001' or symbol_name == 'R-001': # 跳过逆回购
                    continue
                # business_name = parts[header_indices["business"]]
                operation_name = parts[header_indices["operation"]]
                quantity = int(parts[header_indices["quantity"]])
                price = float(parts[header_indices["price"]])
                occur_amount = float(parts[header_indices["occur_amount"]])
                if len(date_str) == 8:
                    formatted_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
                else:
                    formatted_date = date_str
                ts = f"{formatted_date} {time_str}"
                if "卖出" in operation_name or "股息入账" in operation_name:
                    side = "SELL"
                    delta_qty = -abs(quantity)
                elif "买入" in operation_name or "红利税补缴" in operation_name:
                    side = "BUY"
                    delta_qty = abs(quantity)
                else:
                    continue
                records.append({"symbol": symbol_name, "ts": ts, "side": side, "price": f"{price:.3f}", "delta_qty": delta_qty, "occur_amount": occur_amount})
            except (ValueError, IndexError):
                continue
        records.sort(key=lambda x: x["ts"])
        return records

    @staticmethod
    def import_from_statement(records: List[Dict[str, Any]], symbol=None) -> int:
        """
        从解析的交割单记录导入交易
        records: parse_statement_text 返回的记录列表
        symbol: 指定品种代码（如果为None，从记录中提取）
        返回: 成功导入的记录数

        优化说明：
        1. 只加载一次文件，在内存中操作
        2. 追加新记录，过滤重复（时间戳、方向、变动量都相同）
        3. 排序后，从插入点开始重新计算所有后续记录（级联更新）
        4. NEW/RECORD/RESET 保持不变
        5. BUY 且 old_qty=0 时改成 NEW
        6. 其他 BUY/SELL 正常计算
        7. 预览受影响的记录，确认后再保存
        """
        if not records:
            print("交割单中没有可导入的记录")
            return 0
        
        # 如果没有指定品种，使用第一条记录的品种
        if symbol is None:
            symbol = records[0]["symbol"]

        # 过滤出该品种的所有记录
        symbol_records = [r for r in records if r["symbol"] == symbol]
        if not symbol_records:
            print(f"未在交割单中找到品种【{symbol}】")
            return 0

        # 按时间排序
        symbol_records.sort(key=lambda x: x["ts"])

        # 1. 加载数据（只读一次）
        all_data = DataManager.load_trades()
        block = next((b for b in all_data if b["symbol"] == symbol), None) # block 是 all_data 中某个元素的引用，修改 block 就是修改 all_data
        if block is None:
            block = {"symbol": symbol, "records": []}
            all_data.append(block)

        # 获取旧记录的（时间戳、变动量）集合（用于判断重复）
        # 注意：不比较 side，因为原有记录可能是 NEW，交割单解析出来是 BUY，本质是同一笔交易
        old_records_keys = {(r["ts"], int(r["delta_qty"])) for r in block["records"]}

        # 2. 追加新记录到内存
        skipped_count = 0
        cash_records_to_add = []  # 收集需要添加的现金记录
        for record in symbol_records:
            # 检查重复（时间戳、变动量相同）
            '''注: 若新导入记录之间键值相同，不会被去重'''
            record_key = (record["ts"], record["delta_qty"])
            if record_key in old_records_keys:
                skipped_count += 1
                continue

            # 计算变动后的状态
            delta_qty = record["delta_qty"]
            original_price = float(record["price"]) # 原始成交价格

            # 根据发生金额计算临时价格（包含手续费）
            # 发生金额的绝对值 / 数量绝对值 = 实际成本/收入对应的均价
            occur_amount = record["occur_amount"]
            abs_qty = abs(delta_qty)
            temp_price = abs(occur_amount) / abs_qty if abs_qty != 0 else original_price

            # 收集现金记录信息（发生金额：买入为负支出，卖出为正收入）
            # 现金方向与股票相反：买入股票=现金减少(occur_amount<0)，卖出股票=现金增加(occur_amount>0)
            cash_records_to_add.append({
                "ts": record["ts"],
                "occur_amount": occur_amount  # 取反：现金变动方向与股票相反
            })

            # 从内存中反向扫描一次，取到三样东西：
            #   上一条 prev_record / 上上条 prev_prev_record（日内反T清零合并前置状态用）
            #   最近一个非零价格 last_valid_price（红利税补缴/股息入账成交价为0时显示用；
            #   prev_record 本身也可能是一条价格=0的红利记录，所以要继续往前找）
            
            # （等价写法：先切出 earlier 子列表，earlier[-1]/earlier[-2] 取两条记录，
            #   再用 next() 倒序找首个非零价，逐行更直白；单循环只省一次子列表复制，语义相同）
            old_qty, old_invest = 0, 0.0
            prev_record, prev_prev_record = None, None
            last_valid_price = None
            for existing_record in reversed(block["records"]):
                if existing_record["ts"] >= record["ts"]:
                    continue
                # 边界：只跳过"空仓 + 无成交量"的记录（卖光后夹着的股息入账/红利税补缴/RECORD），
                # 它乘出来恒为0、不携带状态，跳过是为了继续往前找到真正的卖出记录（会对均价有影响但这类记录一般不在盘中，不符合日内反T）；
                # 持仓非0、同为0成交量的红利记录不能跳——它的均价已被红利分支调整过
                if int(existing_record["delta_qty"]) == 0 and int(existing_record["current_qty"]) == 0:
                    if last_valid_price is None and float(existing_record["price"]) != 0:
                        last_valid_price = existing_record["price"]
                    continue
                if prev_record is None:
                    # 前置状态：qty × avg_cost_after = 变动前总成本，普通路径都从这里接；
                    # 上一笔是持仓期红利记录时同样适用（取它调整后的均价）
                    prev_record = existing_record
                    old_qty = int(prev_record["current_qty"])
                    old_invest = old_qty * float(prev_record["avg_cost_after"])
                elif prev_prev_record is None:
                    # 上上条只在反T清零结转时用到：取 qty × avg_cost_after 作为卖出前总成本（残值基数）；
                    # 若它是持仓期红利记录也照常参与（均价已含红利/税调整）
                    prev_prev_record = existing_record
                if last_valid_price is None and float(existing_record["price"]) != 0:
                    last_valid_price = existing_record["price"]
                if prev_prev_record is not None and last_valid_price is not None:
                    break

            # 当天反T清零合并：同日内上一条把持仓卖到清零（均价已为0），直接再买入会把成本重置为买入价；
            # 此时把上上条状态与卖出残值合并进前置总成本：残值 = 上上条qty*均价 + 卖出量*含费卖价，
            # 后续统一走同一条成本链。隔日的清零视为真正平仓再重新建仓，不合并。
            # 空仓期间夹着的成交量为0记录已在扫描时跳过，这里的 prev_record 一定是真正的卖出记录
            if (old_qty == 0 and prev_record is not None and prev_prev_record is not None
                    and record["ts"][:10] == prev_record["ts"][:10]
                    and int(prev_record["delta_qty"]) != 0):
                prev_delta = int(prev_record["delta_qty"])
                prev_temp_price = float(prev_record["price"]) 
                # 含费卖价口径同 temp_price：同批导入的上一条可在 symbol_records 里按（时间戳,变动量）找到并
                # 用发生金额还原；历史批次的记录没有发生金额，只能退回存储的成交均价（不含费）
                for sr in symbol_records:
                    if sr["ts"] == prev_record["ts"] and int(sr["delta_qty"]) == prev_delta:
                        prev_temp_price = abs(sr["occur_amount"]) / abs(prev_delta)
                        break
                old_invest = (int(prev_prev_record["current_qty"]) * float(prev_prev_record["avg_cost_after"])
                              + prev_delta * prev_temp_price) # 上上条交易的残值

            # 判断操作类型
            if old_qty == 0:
                side = "NEW"
            else:
                side = record["side"]

            # 计算新状态（总成本口径；calculate_new_state 由 qty*均价 推总成本，
            # 清零残值在 qty=0 时表达不了，故此处内联同一公式）
            current_qty = old_qty + delta_qty
            new_invest = old_invest + delta_qty * temp_price
            avg_cost_after = new_invest / current_qty if current_qty != 0 else 0.0

            # 处理股息入账与红利税补缴业务（成交数量为0，发生金额不为0）
            if abs_qty == 0 and occur_amount != 0:
                avg_cost_after = (old_invest - occur_amount)/old_qty if old_qty != 0 else 0

            # 成交价为0的业务（股息入账/红利税补缴）显示时取上一个有效价格
            display_price = last_valid_price if original_price == 0 and last_valid_price is not None else record["price"]

            block["records"].append({
                "ts": record["ts"],
                "side": side,
                "price": display_price,
                "current_qty": current_qty,
                "delta_qty": delta_qty,
                "avg_cost_after": f"{avg_cost_after:.4f}"
            })

        if skipped_count > 0:
            print(f"跳过 {skipped_count} 条重复记录")

        actual_imported = len(symbol_records) - skipped_count
        if actual_imported == 0:
            print("没有新记录需要导入")
            return 0

        old_count = len(block["records"]) - actual_imported

        # 3. 按时间排序
        block["records"].sort(key=lambda r: r["ts"])

        # 4. 找到需要重新计算的起始位置
        # 找出真正新记录（不在旧记录中的）的最早时间戳
        new_timestamps = [r["ts"] for r in symbol_records if (r["ts"], r["delta_qty"]) not in old_records_keys]
        if not new_timestamps:
            print("没有新记录需要导入")
            return 0
        first_new_ts = min(new_timestamps)

        # 从排序后列表中找第一条时间 >= first_new_ts 的记录，从它开始需要重新计算
        restart_index = 0
        for i, record in enumerate(block["records"]):
            if record["ts"] >= first_new_ts:
                restart_index = i
                break

        # # 5. 级联更新：从 restart_index 开始重新计算
        # for i in range(restart_index, len(block["records"])):
        #     curr_record = block["records"][i]

        #     # NEW/RECORD/RESET 保持不变
        #     if curr_record["side"] in ("NEW", "RECORD", "RESET"):
        #         continue

        #     # 获取前置状态
        #     old_qty, old_avg_cost = 0, 0.0
        #     if i > 0:
        #         prev_record = block["records"][i - 1]
        #         old_qty = int(prev_record["current_qty"])
        #         old_avg_cost = float(prev_record["avg_cost_after"])

        #     delta_qty = int(curr_record["delta_qty"])
        #     price = float(curr_record["price"])

        #     # BUY 且 old_qty=0 时改成 NEW
        #     if curr_record["side"] == "BUY" and old_qty == 0:
        #         curr_record["side"] = "NEW"

        #     # 正常计算（old_qty=0 时 calculate_new_state 会自动重置成本）
        #     new_avg_cost, _, _ = calculate_new_state(old_qty, old_avg_cost, delta_qty, price)
        #     curr_record["current_qty"] = old_qty + delta_qty
        #     curr_record["avg_cost_after"] = f"{new_avg_cost:.4f}"

        # 6. 预览受影响的记录
        print(f"\n即将导入 {actual_imported} 条新记录到品种【{symbol}】")
        print(f"受影响的记录（从原序号 {restart_index+1} 开始，共 {len(block['records']) - restart_index} 条）：")

        # 准备记录数据（添加 symbol 字段，区分新增/原有）
        affected_records = []
        for rec in block["records"][restart_index:]:
            record_key = (rec["ts"], int(rec["delta_qty"]))
            if record_key in old_records_keys:
                symbol_display = "[原有]"
            else:
                symbol_display = "[新增]"
            affected_records.append({
                "symbol": symbol_display,
                **rec
            })

        # 调用 print_records 显示，传入起始序号
        print_records(affected_records, start_index=restart_index+1)

        # 7. 确认是否保存
        confirm = input("\n是否确认保存？[Y]: ").strip().upper()
        if confirm != 'Y':
            print("已取消导入")
            return 0

        # 8. 保存到文件（包含现金记录）
        cash_block = next((b for b in all_data if b["symbol"] == CASH_SYMBOL), None)
        if cash_block is None and cash_records_to_add:
            cash_block = {"symbol": CASH_SYMBOL, "records": []}
            all_data.append(cash_block)
        for cash_rec in cash_records_to_add:
            DataManager.add_cash_record(cash_rec["ts"], cash_rec["occur_amount"], cash_block)
        # 再保存
        DataManager.save_trades(all_data)
        print(f"成功从交割单导入 {actual_imported} 条记录到品种【{symbol}】，并同步更新现金记录")
        return actual_imported


# 向后兼容的别名
export_to_csv = FileHandler.export_to_csv
export_to_json = FileHandler.export_to_json
parse_statement_text = FileHandler.parse_statement_text
import_from_statement = FileHandler.import_from_statement


# ===============================
# 显示功能
# ===============================
class Display:
    """显示类：负责各种格式化显示"""

    @staticmethod
    def print_help() -> None:
        """显示帮助信息"""
        print("\n" + "=" * 50)
        print("交易记录管理工具".center(40))
        print("=" * 50)
        print("""
【注意】
卖出时使用的是调整成本法而非加权移动平均法，即按当前价格从总成本中
扣除卖出份额，然后计算新的总成本和平均成本；而不是按成本价扣除卖出份额。
这相当于把卖出实现的盈利或亏损分摊到剩余份额上，用卖出总金额覆盖或补偿
原始成本的一部分。

因此，可能出现目标均价过低或过高，需要卖出比持仓更多的份额来分摊的情况。
此时做空后的负成本是卖掉所借份额的暂时所得，目标均价是买入归还的盈亏平衡线
（反向，越高越容易实现）。

【操作类型说明】
- BUY:   买入操作
- SELL:  卖出操作
- RESET: 重置品种（重新设置持仓、均价）
- NEW:   建仓买入（从零建立仓位，与 BUY 同样参与匹配与计费）
- RECORD: 记录价格或微调均价（不进行交易）
""")

    @staticmethod
    def get_display_width(s):
        """计算字符串在终端显示的实际宽度"""
        width = 0
        for char in s:
            if unicodedata.east_asian_width(char) in ('W', 'F', 'A'):
                width += 2
            else:
                width += 1
        return width

    @staticmethod
    def pad_string(s, target_width, align='left'):
        """根据显示宽度进行填充"""
        display_width = Display.get_display_width(str(s))
        padding_size = max(0, target_width - display_width)
        if align == 'left':
            return str(s) + ' ' * padding_size
        else:
            return ' ' * padding_size + str(s)

    @staticmethod
    def _build_match_info(records: List[Dict[str, Any]]) -> Tuple[set, Dict]:
        """复用 TradeAnalyzer 识别做T/战略配对，按品种分组分别跑

        返回 (round_trip, match_map):
            round_trip: {(symbol, ts, price)} 纯做T配对记录（未混战略配对，不高亮）
            match_map: {(symbol, ts): {对方记录位置:匹配量}（后续按匹配量降序并转位置列表）} 做T+战略配对关系，位置为 records 列表下标
        """
        from irr_analyzer import TradeAnalyzer, preprocess_records

        round_trip = set()
        match_map: Dict = {}
        by_symbol: Dict[str, List[int]] = {}
        for i, r in enumerate(records):
            by_symbol.setdefault(r['symbol'], []).append(i)

        for symbol, indices in by_symbol.items():
            # run() 内部自行解析 ts/price/delta_qty，直接传原始记录
            processed = preprocess_records([records[i] for i in indices])
            if not processed:
                continue

            analyzer = TradeAnalyzer(processed, 0.0, symbol)
            analyzer.run()

            # 品种内 (ts, 价格) → 记录列表位置（同秒多笔靠价格区分，同秒同价多条取第一条）
            pos_of = {}
            for i in indices:
                if records[i]['side'] == 'RECORD':
                    continue
                pos_of.setdefault((records[i]['ts'], float(records[i]['price'])), i)

            for t in analyzer.t_trades:
                round_trip.add((symbol, t['buy_ts'], t['buy_price']))
                round_trip.add((symbol, t['sell_ts'], t['sell_price']))
            for t in analyzer.strategic_trades:
                # 部分做T+战略的记录保持高亮（降色仅对纯做T记录）
                round_trip.discard((symbol, t['buy_ts'], t['buy_price']))
                round_trip.discard((symbol, t['sell_ts'], t['sell_price']))
            for t in analyzer.t_trades + analyzer.strategic_trades:
                buy_pos = pos_of.get((t['buy_ts'], t['buy_price']))
                sell_pos = pos_of.get((t['sell_ts'], t['sell_price']))
                if buy_pos is not None:
                    m = match_map.setdefault((symbol, t['buy_ts']), {})
                    m[sell_pos] = m.get(sell_pos, 0) + t['qty']
                if sell_pos is not None:
                    m = match_map.setdefault((symbol, t['sell_ts']), {})
                    m[buy_pos] = m.get(buy_pos, 0) + t['qty']

        # 配对按实际匹配量降序（非对方总成交量），显示时取匹配量最大的一笔
        for key, pos_qty in match_map.items():
            match_map[key] = sorted(pos_qty, key=pos_qty.get, reverse=True)#注意对字典排序只保留key（这里是位置）
        return round_trip, match_map

    @staticmethod
    def print_records(records: List[Dict[str, Any]], sortby: str | None = None, start_index: int = 1, color_hierarchy: bool = False) -> None:
        """打印交易记录

        Args:
            records: 交易记录列表
            sortby: 排序字段（可选）
            start_index: 起始序号（默认为 1）
            color_hierarchy: 颜色层级显示（默认为 False）：
                做T记录不高亮，操作列显示配对记录序号（如 SELL-324 / BUY-325等）

        费用：NEW/BUY/SELL 记录按费率计算（品种与交易时间分档），其余操作显示 "-"
        """
        if not records:
            print("暂无交易记录")
            return
        record_num = sum(1 for r in records if r["side"] == "RECORD")
        total_num = len(records)
        print(f"\n{YELLOW}交易记录（共 {total_num} 条，含 {record_num} 条价格记录）{RESET}")
        if color_hierarchy:
            round_trip, match_map = Display._build_match_info(records)
        else:
            round_trip, match_map = None, None
        from irr_analyzer import TradeAnalyzer
        widths = [6, 10, 25, 12, 10, 10, 10, 10, 12]
        header = Display.pad_string("序号", widths[0]) + Display.pad_string("品种", widths[1]) + Display.pad_string("时间", widths[2]) + Display.pad_string("操作", widths[3])
        header += Display.pad_string("价格", widths[4]) + Display.pad_string("变动", widths[5]) + Display.pad_string("费用", widths[6]) + Display.pad_string("持有", widths[7]) + Display.pad_string("均价", widths[8])
        print(header)
        print("-" * (sum(widths)))
        # 保留原始列表位置：match_map 配对序号基于原始位置，排序只影响显示顺序
        indexed = sorted(enumerate(records), key=lambda x: x[1][sortby]) if sortby else list(enumerate(records))
        for i, (_, r) in enumerate(indexed, start_index): # i: 显示顺序，也是indexed的顺序（上一行中排序了） _: 原始索引（即记录在原始列表 records 中的位置）r: 记录本身（字典内容）
            color = RESET
            if r["side"] == "BUY": color = RED
            elif r["side"] == "SELL": color = BLUE
            elif r['side'] == "RESET": color = YELLOW
            elif r['side'] == "NEW": color = CYAN
            if round_trip is not None and (r['symbol'], r['ts'], float(r['price'])) in round_trip:
                color = DARK_COLOR.get(color, color)
            side_text = r['side']
            if match_map:
                partners = match_map.get((r['symbol'], r['ts']))
                if partners:
                    side_text += f"-{partners[0] + start_index}" + ("等" if len(partners) > 1 else "")
            fee_str = "-"
            if r["side"] in ("BUY", "SELL", "NEW"):
                fee_ts = datetime.strptime(r["ts"], "%Y-%m-%d %H:%M:%S")
                fee_str = f"{TradeAnalyzer.calculate_trade_fee(float(r['price']), abs(int(r['delta_qty'])), r['side'], r['symbol'], fee_ts):.2f}"
            row = Display.pad_string(i, widths[0]) + Display.pad_string(r['symbol'], widths[1]) + Display.pad_string(r['ts'], widths[2]) + f"{color}{Display.pad_string(side_text, widths[3])}{RESET}" 
            row += Display.pad_string(r['price'], widths[4]) + f"{color}{Display.pad_string(r['delta_qty'], widths[5])}{RESET}" + Display.pad_string(fee_str, widths[6]) + Display.pad_string(r['current_qty'], widths[7]) + Display.pad_string(r['avg_cost_after'], widths[8])
            print(row)

    @staticmethod
    def print_current_state(symbol: str, num_now: int, avg_cost: float, cur_price: float) -> None:
        """打印当前状态"""
        print("\n" + "-" * 50)
        print(f"{CYAN}【{symbol}】当前状态{RESET}")
        print("-" * 50)
        print(f"当前价格:    {cur_price:.3f}")
        print(f"当前份额:    {num_now}")
        print(f"当前均价:    {avg_cost:.4f}")
        print(f"当前总额:    {num_now * cur_price:.2f}")
        if num_now > 0:
            profit = (cur_price - avg_cost) * num_now
            print(f"浮动盈亏:    {profit:+.2f}")
        print("-" * 50)

    @staticmethod
    def print_warnings(new_avg_cost: float, num_now: int, change_num: int, cur_price: float, avg_cost: float) -> None:
        """打印警告信息"""
        new_num = num_now + change_num
        new_volume = new_avg_cost * new_num if new_num != 0 else 0
        if new_volume < 0 and new_num > 0:
            print("【注意】总成本为负，意味着剩余持仓总额为纯利润")
        if new_num < 0 and cur_price > new_avg_cost:
            print(f"【警告】在亏损的情况下若想使成本低于现价，需清仓后借入 {abs(new_num):.0f} 股做空")
        if new_num < 0 and cur_price < new_avg_cost:
            print(f"【警告】在盈利的情况下若想使成本高于现价？需清仓后借入 {abs(new_num):.0f} 股做空")

    @staticmethod
    def print_symbols_multi_columns(symbols, cols=3, col_width=20):
        """打印多列的数据"""
        symbols_list = list(symbols)
        for i in range(0, len(symbols_list), cols):
            chunk = symbols_list[i : i + cols]
            line = ""
            for j, sym in enumerate(chunk):
                index = i + j + 1
                item_text = f"{index}. {sym}"
                line += Display.pad_string(item_text, col_width)
            print(f"  {line}")

    @staticmethod
    def print_price_sensitivity_table(
        mode: int,
        current_qty: int,
        avg_cost: float,
        cur_price: float,
        change_qty: int = 0,
        target_price: float = 0.0
    ) -> None:
        """
        打印价格波动敏感性表格
        mode: 1=给定变动份额, 2=给定目标均价
        """
        # 根据价格确定变动档位：5元以下（股票）0.25%，5元以上（ETF场内基金）1%
        step = 0.0025 if cur_price < 5 else 0.01
        steps = [-4, -3, -2, -1, 0, 1, 2, 3, 4]  # 9档

        if mode == 1:
            # 模式1：固定变动份额，显示不同价格下的结果
            print(f"\n计算结果（调仓份额: {change_qty:.0f}）")
            print("-" * 64)
            print(f"{'价格波动':^6}  {'价格':^8}  {'总额变动':^8}  {'调仓后总额':^7}  {'调仓后成本':^5}")
            print("-" * 64)

            for s in steps:
                price = cur_price * (1 + s * step)
                new_avg_cost, _, new_volume = calculate_new_state(
                    current_qty, avg_cost, change_qty, price
                )
                pnl = change_qty * price  # 变动盈亏

                pct = s * step * 100
                pct_str = f"{pct:+.2f}%" if s != 0 else "  0.00%"

                if s == 0:
                    print(f"{YELLOW}{pct_str:^10}  {price:^10.3f}  {pnl:^+12.2f}  {new_volume:^12.2f}  {new_avg_cost:^10.4f}{RESET}")
                else:
                    print(f"{pct_str:^10}  {price:^10.3f}  {pnl:^+12.2f}  {new_volume:^12.2f}  {new_avg_cost:^10.4f}")

        elif mode == 2:
            # 模式2：固定目标均价，显示不同价格下需要的变动份额
            print(f"\n计算结果（目标均价: {target_price:.4f}）")
            print("-" * 64)
            print(f"{'价格波动':^6}  {'价格':^8}  {'需调仓份额':^7}  {'调仓后总额':^7}  {'调仓后成本':^5}")
            print("-" * 64)

            volume = current_qty * avg_cost
            for s in steps:
                price = cur_price * (1 + s * step)

                if abs(target_price - price) < 0.0001: # 如果目标价格和当前价格几乎相同，避免除以零或极小数导致的异常
                    if s == 0:
                        print(f"{YELLOW}{'  0.00%':^10}  {price:^10.3f}  {'--':^12}  {'--':^12}  {'--':^10}{RESET}")
                    else:
                        print(f"{'  0.00%':^10}  {price:^10.3f}  {'--':^12}  {'--':^12}  {'--':^10}")
                    continue # 跳过后续计算

                calc_change = int((target_price * current_qty - volume) / (price - target_price))
                if PRACTICE:
                    calc_change = round(calc_change / 100) * 100

                new_avg_cost, _, new_volume = calculate_new_state(current_qty, avg_cost, calc_change, price)

                pct = s * step * 100
                pct_str = f"{pct:+.2f}%" if s != 0 else "  0.00%"

                if s == 0:
                    print(f"{YELLOW}{pct_str:^10}  {price:^10.3f}  {calc_change:^+12.0f}  {new_volume:^12.2f}  {new_avg_cost:^10.4f}{RESET}")
                else:
                    print(f"{pct_str:^10}  {price:^10.3f}  {calc_change:^+12.0f}  {new_volume:^12.2f}  {new_avg_cost:^10.4f}")


# 向后兼容的别名
print_help = Display.print_help
get_display_width = Display.get_display_width
pad_string = Display.pad_string
print_records = Display.print_records
print_current_state = Display.print_current_state
print_warnings = Display.print_warnings
print_symbols_multi_columns = Display.print_symbols_multi_columns


# ===============================
# 输入辅助函数
# ===============================
class InputHelper:
    """输入辅助类：负责各种输入处理"""

    @staticmethod
    def input_with_default(prompt: str, default: float, accu=3) -> float:
        """输入函数，显示默认值，直接回车则使用默认值"""
        input_str = input(f"{prompt}[默认: {default:.{accu}f}]: ").strip()
        if not input_str:
            return default
        try:
            return float(input_str)
        except ValueError:
            print(f"输入无效，使用默认值 {default:.{accu}f}")
            return default

    @staticmethod
    def get_input_time(prompt: str, default_time: str = "00:00:00") -> Optional[str]:
        """时间输入辅助函数，支持多种格式，返回YY-MM-DD HH:MM:SS|None"""
        val = input(prompt).strip()
        if not val: # 如果输入为空
            return None
        val = val.replace("t", datetime.now().strftime('%Y-%m-%d'))
        val = val.replace("T", datetime.now().strftime('%Y-%m-%d'))
        if len(val) == 5: # 如果只有时分秒
            val = datetime.now().strftime('%Y-%m-%d')[:4] + '-' + val
        if len(val) == 10: # 如果只有年月日
            return f"{val} {default_time}"
        if ":" in val and "-" not in val: # 如果只有时分，缺少秒
            if len(val.split(':')) == 2:
                val += ":00"
            today = datetime.now().strftime('%Y-%m-%d')
            return f"{today} {val}"
        if len(val.split(':')) == 2: # 如果只缺秒
            val += ":00"
        return val

    @staticmethod
    def parse_batch_indices(input_str: str) -> List[int]:
        """
        解析批量删除输入，支持混合格式
        输入格式: "1 3 5 7-9" 表示删除序号 1, 3, 5, 7, 8, 9
        返回: 索引列表（从0开始，即自动减1）
        """
        indices = []
        parts = input_str.replace(',', ' ').split()

        for part in parts:
            if '-' in part:
                # 处理范围，如 7-9
                try:
                    start, end = part.split('-')
                    start_idx = int(start)
                    end_idx = int(end)
                    if start_idx <= end_idx:
                        indices.extend(range(start_idx, end_idx + 1))
                except ValueError:
                    continue
            else:
                # 处理单个数字
                try:
                    indices.append(int(part))
                except ValueError:
                    continue

        # 转换为从0开始的索引，去重并排序
        return sorted(set(i - 1 for i in indices if i > 0))


# 向后兼容的别名
input_with_default = InputHelper.input_with_default
get_input_time = InputHelper.get_input_time


# ===============================
# 交互式导入交割单
# ===============================
def interactive_import_statement(external_target_symbol=None) -> None:
    """
    交互式导入交割单
    symbol: 指定品种代码（在品种菜单中调用时使用）
    """
    print("\n--- 交割单导入（支持多品种混合的交割单） ---")
    print("请粘贴交割单内容（直接从交易软件复制）")
    print("提示：可以多次粘贴，空行（直接回车）结束")

    all_text = []  # 收集所有文本
    paste_count = 0

    while True:
        print(f"\n【第 {paste_count + 1} 次粘贴】")
        text = input("粘贴内容（直接回车结束）: ")

        # 空行表示结束
        if not text.strip():
            break

        # 收集文本，不立即解析
        all_text.append(text)
        paste_count += 1

    if not all_text:
        print("没有输入内容")
        return

    # 合并所有文本并统一解析
    combined_text = '\n'.join(all_text)

    # 检查是否包含表头
    if not any("成交日期" in line and "证券代码" in line for line in all_text):
        print("错误：未检测到表头，请确保粘贴的内容包含表头行")
        print("表头应包含：成交日期、成交时间、证券代码等字段")
        return

    records = parse_statement_text(combined_text)

    if not records:
        print("未识别到有效记录，请检查格式")
        return

    print(f"\n交割单中识别到 {len(records)} 条记录")
    for r in records:
        print(f"  {r['ts']} {r['symbol']} {r['side']} {r['delta_qty']} @ {r['price']}")

    all_records = records  # 使用解析结果

    if not all_records:
        print("没有可导入的记录")
        return

    # 确定品种
    if not external_target_symbol: # 如果调用函数时已传入品种就直接作为目标品种，否则则使用第一个记录的品种
        target_symbol = all_records[0]['symbol']
    # 多品种循环导入
    symbols_list = list(set(r["symbol"] for r in all_records))
    symbos_database,_ = list_symbols()
    while symbols_list:
        print("交割单中识别到的品种: ")
        for i, symbol_recognized in enumerate(symbols_list):
            print(f"{i+1}.{symbol_recognized}", end=' ')
        print("")

        # 选择要导入的品种
        symbol_record = external_target_symbol if external_target_symbol in symbols_list else symbols_list[0]
        # if len(symbols_list) > 1:
        symbol_input = input(f"\n导入交割单中的哪个品种？[默认: {symbol_record}] ").strip().upper()
        if symbol_input: # 不用else是因为symbol_record已定义
            if symbol_input.isdigit() and 1 <= int(symbol_input) <= len(symbols_list):
                symbol_record = symbols_list[int(symbol_input) - 1]
            else:
                symbol_record = symbol_input
        target_symbol = external_target_symbol if external_target_symbol in symbols_list else symbol_record
        # 选择目标品种
        symbols, volumes = list_symbols()
        # print(f"数据库中已有品种: {', '.join(symbols)}")
        print(f"数据库中已有品种: ")
        for i, symbol_database in enumerate(symbols):
            print(f"{i+1}.{symbol_database}", end=' ')
        print("")
        target_input = input(f"导入至数据库中哪个品种（若不存在会自动新建）？[默认: {target_symbol}] ").strip().upper()
        if target_input: # 不用else是因为target_symbol已定义
            if target_input.isdigit() and 1 <= int(target_input) <= len(symbols):
                target_symbol = symbols[int(target_input) - 1]
            else:
                target_symbol = target_input


        

        # 确认导入
        confirm = input(f"\n确认将交割单中的【{symbol_record}】导入至数据库中的【{target_symbol}】？[Y]: ").strip().upper()
        if confirm == "Y":
            # 品种名不一致时更正
            if target_symbol != symbol_record:
                for r in all_records:
                    if r["symbol"] == symbol_record:
                        r["symbol"] = target_symbol
            import_record_num=import_from_statement(all_records, target_symbol)
            if import_record_num == 0:
                delete=input(f"导入失败，是否删除交割单中【{target_symbol}】的记录？[Y]: ").upper().strip()
                if delete == "Y":
                    all_records = [r for r in all_records if r["symbol"] != target_symbol]
            else:
                all_records = [r for r in all_records if r["symbol"] != target_symbol]
            symbols_list = list(set(r["symbol"] for r in all_records))

            if symbols_list:
                next_import = input("\n继续导入下一个品种？[Y]: ").strip().upper()
                if next_import != "Y":
                    break
        else:
            print("已取消导入")
            delete=input(f"是否删除交割单中【{symbol_record}】的记录？[Y]: ").upper().strip()
            if delete == "Y":
                all_records = [r for r in all_records if r["symbol"] != symbol_record]
                print(f"删除成功")
            symbols_list = list(set(r["symbol"] for r in all_records))
            if symbols_list:
                next_import = input("\n继续导入下一个品种？[Y]: ").strip().upper()
                if next_import != "Y":
                    break


# ===============================
# 菜单
# ===============================
def main_menu(symbol: str) -> None:
    try:
        import ctypes
        import sys
        if sys.platform == "win32":
            ctypes.windll.kernel32.SetConsoleTitleW(f"{symbol}")
    except:
        pass

    """主菜单"""
    # 获取当前状态
    current_qty, avg_cost = get_current_state(symbol)
    default_price = get_last_price(symbol)
    change_qty = 0
    target_price = avg_cost

    # 如果没有价格记录，需要输入
    if default_price == 0.0:
        print(f"\n首次记录品种 {symbol}")
        try:
            cur_price = float(input("首次价格: "))
            current_qty = int(input_with_default("首次份额（手）", 0))*100
            avg_cost = input_with_default("首次均价", 0)
            before = input("是否为补录？[Y]: ").upper()
            if before == "Y":
                ts = get_input_time("首次时间 (如(2026-)01-25 HH:MM(:SS)，留空默认为当前时间；\n  日期可替换成t(oday)，仅日期则时间为15:30，仅时间则日期为今天): ", "15:30:00")
            else:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            print("输入无效")
            return

        add_trade(symbol, {
            "ts": ts,
            "side": "NEW",
            "price": f"{cur_price:.3f}",
            "current_qty": current_qty,
            "delta_qty": current_qty,
            "avg_cost_after": f"{avg_cost:.4f}"
        })
        print(f"\n已记录 {symbol} 首次价格: {cur_price:.3f}，首次份额: {current_qty}，首次成本: {avg_cost:.3f}")
        # return
    else:
        cur_price = default_price
    
    last_choice="0-6"
    while True:
        # 每次循环重新加载份额和均价，确保与文件数据一致
        # 但保留 cur_price（用户可能在操作中修改过最新的实时价格）
        current_qty, avg_cost = get_current_state(symbol)
        print_current_state(symbol, current_qty, avg_cost, cur_price)

        print("""
【操作菜单】
1. 给定调仓份额，计算调仓后均价
2. 给定目标均价，计算需调仓份额
3. 查看、修改、导入和导出交易记录
4. RESET重置品种（重置持仓、均价、价格）
5. RECOED记录价格或微调均价（不调整持仓）
6. 投资回报率(ROI)与内部收益率(IRR)分析
0. 返回主菜单 / 切换品种
        """)

        try:            
            choice = input(f"请选择操作 [{last_choice}]: ").strip()
            if choice=="":
                choice = last_choice.replace("默认:","")

            if choice == "0":
                return

            elif choice == "1":
                # 模式1：给定变动份额
                print("\n--- 模式1：给定变动份额 ---")
                change_qty = int(input_with_default("变动份额（手）", change_qty//100, accu=0))*100

                # 显示当前价格，允许调整
                # print(f"\n当前价格: {cur_price:.3f}")
                cur_price = input_with_default("当前价格", cur_price)

                new_avg_cost, current_volume, new_volume = calculate_new_state(
                    current_qty, avg_cost, change_qty, cur_price
                )

                # print(f"\n变动份额: {change_qty:.0f} @ {cur_price:.3f}")
                # print(f"当前总额: {current_volume:.2f}")
                # print(f"变动后总额: {new_volume:.2f}")
                # print(f"变动后平均成本: {new_avg_cost:.4f}")

                print_warnings(new_avg_cost, current_qty, change_qty, cur_price, avg_cost)

                # 打印价格波动敏感性表格
                Display.print_price_sensitivity_table(1, current_qty, avg_cost, cur_price, change_qty)

                # 确认操作
                if current_qty == 0:
                    side = "NEW"
                
                else:
                    side = "BUY" if change_qty > 0 else "SELL"
                confirm = input(f"\n确认{side}操作？[Y]: ").strip().upper()

                if confirm == "Y":
                    current_qty += change_qty
                    avg_cost = new_avg_cost
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    add_trade(symbol, {
                        "ts": ts,
                        "side": side,
                        "price": f"{cur_price:.3f}",
                        "current_qty": current_qty,
                        "delta_qty": change_qty,
                        "avg_cost_after": f"{avg_cost:.4f}"
                    })
                    print("操作成功！")


            elif choice == "2":
                # 模式2：给定目标均价
                print("\n--- 模式2：给定目标均价 ---")

                target_price = float(input_with_default("目标均价", target_price, accu=4))

                # 显示当前价格，允许调整
                # print(f"\n当前价格: {cur_price:.3f}")
                cur_price = input_with_default("当前价格", cur_price)

                if target_price == cur_price:
                    print("当前价格等于目标均价，无法计算")
                    continue

                volume = current_qty * avg_cost
                change_qty = int((target_price * current_qty - volume) / (cur_price - target_price))
                if PRACTICE:
                    change_qty = round(change_qty / 100) * 100

                new_avg_cost, current_volume, new_volume = calculate_new_state(
                    current_qty, avg_cost, change_qty, cur_price
                )

                # print(f"\n变动份额: {change_qty:.0f} ({change_qty // 100}手)")
                # print(f"\n变动份额: {change_qty:.0f} @ {cur_price:.3f}")
                # print(f"当前总额: {current_volume:.2f}")
                # print(f"变动后总额: {new_volume:.2f}")
                # print(f"变动后平均成本: {new_avg_cost:.4f}")

                print_warnings(new_avg_cost, current_qty, change_qty, cur_price, avg_cost)

                # 打印价格波动敏感性表格
                Display.print_price_sensitivity_table(2, current_qty, avg_cost, cur_price, change_qty=change_qty, target_price=target_price)

                side = "BUY" if change_qty > 0 else "SELL"
                confirm = input(f"\n确认{side}操作？[Y]: ").strip().upper()

                if confirm == "Y":
                    current_qty += change_qty
                    avg_cost = new_avg_cost
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    add_trade(symbol, {
                        "ts": ts,
                        "side": side,
                        "price": f"{cur_price:.3f}",
                        "current_qty": current_qty,
                        "delta_qty": change_qty,
                        "avg_cost_after": f"{avg_cost:.4f}"
                    })
                    print("操作成功！")


            elif choice == "3":
                # 查看、修改和导出交易记录
                view_export_menu(symbol)

            elif choice == "4":
                # 重置品种（重新设置持仓、均价、价格）
                print("\n--- 重置品种 ---")
                print(f"当前状态: 份额={current_qty}, 均价={avg_cost:.4f}, 价格={cur_price:.3f}")

                new_qty = int(input("新的份额: "))
                new_avg = input_with_default("新的均价", avg_cost)

                # 显示当前价格，允许调整
                # print(f"\n当前价格: {cur_price:.3f}")
                new_price = input_with_default("新的价格", cur_price)

                confirm = input("\n确认重置？[Y]: ").strip().upper()
                if confirm == "Y":
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    add_trade(symbol, {
                        "ts": ts,
                        "side": "RESET",
                        "price": f"{new_price:.3f}",
                        "current_qty": new_qty,
                        "delta_qty": new_qty,
                        "avg_cost_after": f"{new_avg:.4f}"
                    })
                    avg_cost = new_avg
                    cur_price = new_price
                    print("品种已重置")

            elif choice == "5":
                # 仅记录价格
                ts = get_input_time("记录时间 (如(2026-)01-25 HH:MM(:SS)，留空默认为当前时间；\n  日期可替换成t(oday)，仅日期则时间为15:00，仅时间则日期为今天): ", "15:00:00")
                if not ts:
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    # 使用当前状态
                    record_qty, record_avg_cost = current_qty, avg_cost
                else:
                    # 补录：获取该时间点之前的状态
                    record_qty, record_avg_cost = get_current_state(symbol, ts)

                print(f"\n时间点: {ts}")
                print(f"持有份额: {record_qty}")
                cur_price = input_with_default("新的价格", cur_price)
                print(f"记录均价: {record_avg_cost:.4f}")
                record_avg_cost = input_with_default("新的均价", record_avg_cost, accu=4)

                add_trade(symbol, {
                    "ts": ts,
                    "side": "RECORD",
                    "price": f"{cur_price:.3f}",
                    "current_qty": record_qty,
                    "delta_qty": 0,
                    "avg_cost_after": f"{record_avg_cost:.4f}"
                })
                print(f"已记录价格: {cur_price:.3f}，均价: {record_avg_cost:.4f}")

            elif choice == "6":
                # IRR 内部收益率分析
                irr_analysis_interactive(symbol, cur_price)

            last_choice = "默认:"+choice
            

        except ValueError as e:
            print(f"数值无效: {e}")
        except KeyboardInterrupt:
            # print("\n\n操作已取消")
            return
        except Exception as e:
            print(f"\n{'\033[91m'}发生错误: {e}{'\033[0m'}")

def view_export_menu(symbol: str) -> None:
    """查看和导出菜单"""
    while True:
        print("\n" + "-" * 50)
        print("""【数据库菜单】
1. 筛选当前品种记录
2. 筛选所有品种记录
3. 新增/修改/删除记录
4. 批量删除记录
5. 从交割单导入
6. ROI 与 IRR 分析
7. 重命名品种
0. 返回
        """)

        choice = input("请选择 [0-7]: ").strip()

        if choice == "0":
            return

        elif choice in ["1", "2"]:
            # 筛选记录（1和2共用代码，区别只在品种输入方式）
            print("\n--- 筛选条件（留空表示不限制，多种条件以英文逗号分隔） ---")

            # 品种筛选（1和2的区别）
            if choice == "1":
                # 当前品种：使用 input_with_default
                # symbol_input = input(f"品种 [默认: {symbol}]: ").strip()
                # if symbol_input:
                #     symbols = {s.strip().upper() for s in symbol_input.split(",")}
                # else:
                symbols = {symbol}
            else:
                # 所有品种：普通 input，空表示全部
                symbol_input = input("品种 [默认: 全部]: ").strip()
                if symbol_input:
                    symbols = {s.strip().upper() for s in symbol_input.split(",")}
                else:
                    symbols = None

            # 操作类型筛选（支持逗号分隔）
            side_input = input("操作类型 (BUY/SELL/RESET/NEW/RECORD): ").strip().upper()
            sides = None
            if side_input:
                sides = {s.strip() for s in side_input.split(",")}

            # 时间范围筛选
            print("下面输入筛选时间(如(2026-)01-25 HH:MM(:SS)，留空表示不限；\n日期可替换成t(oday)，仅日期会补充默认时间，仅时间则日期为今天): ")
            start_ts = get_input_time("起始时间 (默认：00:00:00): ", "00:00:00")
            end_ts = get_input_time("结束时间 (默认：23:59:59): ", "23:59:59")
            sortby, exclude_cash = None, False
            if choice == "2":
                sortby = "ts" if input("是否按时间排序？[Y]: ").upper()=="Y" else None
                exclude_cash = input("是否排除现金品种？[Y]: ").upper()=="Y" 
                

            filtered = filter_trades(symbols=symbols, sides=sides, start_ts=start_ts, end_ts=end_ts)

            if filtered:
                if sortby:
                    filtered = sorted(filtered, key=lambda x: x["ts"])
                if exclude_cash:
                    filtered = [r for r in filtered if r["symbol"] != CASH_SYMBOL]
                print_records(filtered, color_hierarchy = True)
                action = input("\n操作 (1=导出 2=批量删除, 回车跳过): ").strip()
                if action == "1":
                    fmt = input("导出格式 (1=CSV, 2=JSON): ").strip()
                    if fmt == "1":
                        filename = input("文件名 [默认: filtered_export_时间.csv]: ").strip()
                        if not filename:
                            filename = "filtered_export"
                        export_to_csv(filtered, filename+f"_{datetime.now().strftime('%Y%m%d')}.csv")
                    elif fmt == "2":
                        filename = input("文件名 [默认: filtered_export_时间.json]: ").strip()
                        if not filename:
                            filename = "filtered_export"
                        export_to_json(filtered, filename+f"_{datetime.now().strftime('%Y%m%d')}.json")
                elif action == "2":
                    delete_input = input("输入要删除的序号（可含现金，会自动跳过），用空格或','分隔，连续区间用'-'连接（如'1 3 5-7'），q取消：").strip()
                    if delete_input.lower() == "q":
                        continue
                    indices = InputHelper.parse_batch_indices(delete_input)
                    if not indices:
                        print("无效的输入")
                        continue
                    valid_indices = [i for i in indices if 0 <= i < len(filtered)]
                    if not valid_indices:
                        print("没有有效的序号")
                        continue
                    # 按 symbol 分组
                    symbol_groups = {}
                    for idx in valid_indices:
                        rec = filtered[idx]
                        symbol_groups.setdefault(rec["symbol"], []).append(rec)
                    # 预览
                    print(f"\n将删除以下 {len(valid_indices)} 条记录：")
                    for idx in sorted(valid_indices):
                        rec = filtered[idx]
                        print(f"  {idx + 1}. {rec['symbol']} {rec['ts']} {rec['side']} {rec['delta_qty']} @{rec['price']}")
                    confirm = input("\n确认删除？[Y]: ").strip().upper()
                    if confirm != "Y":
                        print("已取消")
                        continue
                    # 按 symbol 反查原始索引并删除（跳过现金品种，由 delete_trades_batch 同步删除）
                    total_deleted = 0
                    for sym, recs in symbol_groups.items():
                        if sym == CASH_SYMBOL:
                            continue
                        data = DataManager.load_trades()
                        block = next((b for b in data if b["symbol"] == sym), None)
                        if not block:
                            continue
                        target_keys = {(r["ts"], int(r["delta_qty"]), int(r["current_qty"])) for r in recs}
                        original_indices = [i for i, r in enumerate(block["records"])
                                            if (r["ts"], int(r["delta_qty"]), int(r["current_qty"])) in target_keys]
                        if original_indices:
                            total_deleted += DataManager.delete_trades_batch(sym, original_indices)
                    print(f"成功删除 {total_deleted} 条非现金记录，现金记录同步删除")
            else:
                print("没有符合条件的数据")

        elif choice == "3":
            # 修改/新增记录
            block = get_symbol_block(symbol)
            if block and block["records"]:
                print_records([{"symbol": symbol, **r} for r in block["records"]], color_hierarchy = True)
            else:
                print("暂无记录")

            # print("\n0: 新增记录")
            index = input("\n要修改的记录序号（从1开始，N新增；D[序号]快捷删除，回车退出）: ")

            if index.isdigit():
                index = int(index)-1

                if block and 0 <= index < len(block["records"]):
                    # 修改现有记录
                    print(f"\n当前记录: {block['records'][index]}")
                    print("输入新值（留空保持不变）:")

                    updates = {}
                    new_ts = get_input_time(f"时间 [{block['records'][index]['ts']}]: ","15:30:00")
                    if new_ts:
                        updates["ts"] = new_ts

                    new_side = input(f"操作类型 (BUY/SELL/RESET/NEW/RECORD) [{block['records'][index]['side']}]: ").strip().upper()
                    if new_side:
                        new_side = new_side.upper()
                        updates["side"] = new_side

                    new_price = input(f"价格 [{block['records'][index]['price']}]: ").strip()
                    if new_price:
                        try:
                            updates["price"] = f"{float(new_price):.3f}"
                        except ValueError:
                            pass

                    new_delta = input(f"变动份额 [{block['records'][index]['delta_qty']}]: ").strip()
                    if new_delta:
                        try:
                            updates["delta_qty"] = int(new_delta)
                        except ValueError:
                            pass

                    new_qty = input(f"变动后份额 [{block['records'][index]['current_qty']}]: ").strip()
                    if new_qty:
                        try:
                            updates["current_qty"] = int(new_qty)
                        except ValueError:
                            pass
                    
                    new_avg = input(f"变动后均价 [{block['records'][index]['avg_cost_after']}]: ").strip()
                    if new_avg:
                        try:
                            updates["avg_cost_after"] = f"{float(new_avg):.4f}"
                        except ValueError:
                            pass

                    if updates:
                        if update_trade(symbol, index, **updates):
                            print("修改成功！")
                        else:
                            print("修改失败！")
                    else:
                        print("未做任何修改")
                else:
                    print("无效的序号")
            else:
                if index == "":
                    pass
                elif index[0].upper() == "N":
                    # 新增记录
                    print("\n--- 新增记录 ---")
                    print("输入各字段值（留空使用默认值）:")

                    # 时间输入
                    ts = get_input_time("时间 (如(2026-)01-25 HH:MM(:SS)，留空默认为当前时间；\n  t(oday)/仅日期则时间为15:30，仅时间则日期为今天): ","15:30:00")
                    if not ts:
                        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    side = input("操作类型 (BUY/SELL/RESET/NEW/RECORD) [默认: RECORD]: ").strip().upper() or "RECORD"
                    if side == "B":
                        side = "BUY"
                    elif side == "S":
                        side = "SELL"
                    price = input(f"价格 [默认: {get_last_price(symbol):.3f}]: ").strip()
                    if not price:
                        price = f"{get_last_price(symbol):.3f}"
                    else:
                        price = f"{float(price):.3f}"

                    delta_qty_input = input("变动份额 [默认: 0]: ").strip()
                    delta_qty = int(delta_qty_input) if delta_qty_input else 0

                    # if side == "BUY" or side == "SELL" or side == "RECORD" or side == "NEW":
                    old_qty,old_avg_cost=get_current_state(symbol,ts)
                    current_qty_input = input(f"变动后份额 [默认: {old_qty + delta_qty}]: ").strip()
                    current_qty = int(current_qty_input) if current_qty_input else old_qty + delta_qty

                    default_after_avg_cost,_,_ = calculate_new_state(old_qty,old_avg_cost,delta_qty,float(price))
                    avg_cost_input = input(f"变动后均价 [默认: {default_after_avg_cost:.4f}]: ").strip()
                    avg_cost = f"{float(avg_cost_input):.4f}" if avg_cost_input else f"{default_after_avg_cost:.4f}"
                    # else:
                    #     current_qty_input = input("变动后份额 [默认: 0]: ").strip()
                    #     current_qty = int(current_qty_input) if current_qty_input else 0

                    #     avg_cost_input = input("变动后均价 [默认: 0.0000]: ").strip()
                    #     avg_cost = f"{float(avg_cost_input):.4f}" if avg_cost_input else "0.0000"

                    # avg_cost_input = input("变动后均价 [默认: 0.0000]: ").strip()
                    '''
                    if side == "BUY" or side == "SELL" or "RECORD":
                        old_qty,old_avg_cost=get_current_state(symbol,ts)
                        default_after_avg_cost,_,_ = calculate_new_state(old_qty,old_avg_cost,delta_qty,float(price))
                        avg_cost_input = input(f"变动后均价 [默认: {default_after_avg_cost:.4f}]: ").strip()
                        avg_cost = f"{float(avg_cost_input):.4f}" if avg_cost_input else f"{default_after_avg_cost:.4f}"
                    else:
                        avg_cost_input = input("变动后均价 [默认: 0.0000]: ").strip()
                        avg_cost = f"{float(avg_cost_input):.4f}" if avg_cost_input else "0.0000"
                    '''
                    record = {
                        "ts": ts,
                        "side": side,
                        "price": price,
                        "current_qty": current_qty,
                        "delta_qty": delta_qty,
                        "avg_cost_after": avg_cost
                    }

                    add_trade(symbol, record)
                    print("新增记录成功！")
                elif index[0].upper() == "D":
                    if index[1:].isdigit():
                        delete_index = int(index[1:])-1
                        confirm = input(f"确认删除序号 {delete_index + 1} 的记录？[Y]: ").strip().upper()
                        if confirm == "Y":
                            if delete_trade(symbol, delete_index):
                                print("删除成功！")
                            else:
                                print("删除失败！")
                        else:
                            print("取消删除")
                    else:
                        print("无效的序号")

        # elif choice == "4":
        #     # 删除记录
        #     block = get_symbol_block(symbol)
        #     if not block or not block["records"]:
        #         print("暂无记录可删除")
        #         continue

        #     print_records([{"symbol": symbol, **r} for r in block["records"]])
        #     index = int(input("\n要删除的记录序号（从1开始）: ")) - 1

        #     if 0 <= index < len(block["records"]):
        #         confirm = input(f"确认删除序号 {index + 1} 的记录？[Y]: ").strip().upper()
        #         if confirm == "Y":
        #             if delete_trade(symbol, index):
        #                 print("删除成功！")
        #             else:
        #                 print("删除失败！")
        #     else:
        #         print("无效的序号")

        elif choice == "4":
            # 批量删除记录

            block = get_symbol_block(symbol)
            if block and block["records"]:
                print_records([{"symbol": symbol, **r} for r in block["records"]], color_hierarchy = True)
            else:
                print("暂无记录可删除")
                continue

            delete_input = input("\n输入要删除的记录序号，用空格或','分隔，连续区间用'-'连接，支持混合输入，如'1 3 5 7-9'：").strip()

            if delete_input.lower() == "q":
                continue

            indices = InputHelper.parse_batch_indices(delete_input)
            if not indices:
                print("无效的输入")
                continue

            # 验证索引范围
            valid_indices = [i for i in indices if 0 <= i < len(block["records"])]
            if not valid_indices:
                print("没有有效的序号")
                continue

            # 显示将要删除的记录
            print(f"\n将删除以下 {len(valid_indices)} 条记录：")
            for idx in sorted(valid_indices):
                rec = block["records"][idx]
                print(f"  {idx + 1}. {rec['ts']} {rec['side']} {rec['delta_qty']} @{rec['price']}")

            confirm = input("\n确认删除？[Y]: ").strip().upper()
            if confirm == "Y":
                deleted = DataManager.delete_trades_batch(symbol, valid_indices)
                print(f"成功删除 {deleted} 条记录")
            else:
                print("已取消")

        elif choice == "5":
            # 导入交割单
            interactive_import_statement(symbol)

        elif choice == "6":
            # IRR 内部收益率分析
            irr_analysis_interactive(symbol, get_last_price(symbol))

        elif choice.lower() == "7":
            # 重命名品种
            print(f"\n当前品种名称: {symbol}")
            new_name = input("请输入新的品种名称: ").strip().upper()
            if new_name:
                confirm = input(f"确认将品种【{symbol}】重命名为【{new_name}】？[Y]: ").strip().upper()
                if confirm == "Y":
                    if DataManager.rename_symbol(symbol, new_name):
                        print(f"品种【{symbol}】已重命名为【{new_name}】")
                        symbol = new_name
                    else:
                        print("重命名失败")
            else:
                print("品种名称不能为空")


# ===============================
# 入口
# ===============================
def main():
    """主入口"""
    
    print_help()
    ensure_weekly_backup()
    show_zero_volume_symbols = False

    while True:
        # 设置窗口标题
        try:
            import ctypes
            import sys
            if sys.platform == "win32":
                ctypes.windll.kernel32.SetConsoleTitleW("交易记录管理工具")
        except:
            pass
        print("\n" + "=" * 90)
        print("【主菜单】")
        symbols,volumes = list_symbols()
        symbols_with_volume = [
            f"{sym}[{vol:.2f}]"
            for sym, vol in zip(symbols,volumes)
            if show_zero_volume_symbols or vol != 0
        ]
        try:
            if symbols:
                # 获取所有品种中最后一条记录并显示
                data = DataManager.load_trades()
                all_records = [(block["symbol"], block["records"][-1]) for block in data if (block["records"] and block["symbol"]!=CASH_SYMBOL)]
                if all_records:
                    last_symbol, last_record = max(all_records, key=lambda x: x[1]["ts"])
                    print(f"最后记录: {last_symbol} {last_record['side']} {last_record['delta_qty']} @ {last_record['price']} ({last_record['ts']})")
                print("已有品种:")
                print_symbols_multi_columns(symbols_with_volume,cols=4,col_width=23)
                # for i, sym in enumerate(symbols, 1):
                #     print(f"  {i}. {sym}")
                print("-" * 90)
                print(f"  n. 新建品种      d. 删除品种     c. {'显示' if not show_zero_volume_symbols else '隐藏'}清仓品种")
                print("  i. 导入交割单    h. 持仓变化     q. 退出程序")
                print("  r. 组合复盘")
                print("  backup. 手动备份（每7天自动备份）")
                print("-" * 90)

                choice = input("请输入品种编号/名称或操作选项: ").strip()

                if choice.lower() == "q":
                    # print("再见！")
                    break
                elif choice == "n":
                    symbol = input("请输入品种名称: ").strip().upper()
                    if symbol:
                        main_menu(symbol)
                elif choice.lower() == "i":
                    # 导入交割单（不指定品种）
                    interactive_import_statement()
                elif choice.lower() == "h":
                    # 持仓仓位历史分析
                    position_history_interactive()
                elif choice.lower() == "r":
                    # 组合复盘（跨品种区间分析）
                    portfolio_review_interactive()
                elif choice.lower() == "c":
                    show_zero_volume_symbols = not show_zero_volume_symbols
                elif choice.lower() == "d":
                    # 删除品种
                    del_index = input(f"请输入要删除的品种序号 (1-{len(symbols)}): ").strip()
                    if del_index.isdigit() and 1 <= int(del_index) <= len(symbols):
                        del_symbol = symbols[int(del_index) - 1]
                        block = get_symbol_block(del_symbol)
                        if block:
                            print(f"\n品种 {del_symbol} 包含 {len(block['records'])} 条记录")
                            confirm = input(f"确认删除品种 {del_symbol} 及其所有记录？[Y]: ").strip().upper()
                            if confirm == "Y":
                                if delete_symbol(del_symbol):
                                    print(f"已删除品种 {del_symbol}")
                                else:
                                    print("删除失败")
                            else:
                                print("已取消")
                    else:
                        print("无效的序号")
                elif choice.isdigit() and 1 <= int(choice) <= len(symbols):
                    symbol = symbols[int(choice) - 1]
                    main_menu(symbol)
                elif choice.lower() == "backup":
                    confirm = input(f"确认手动备份为 trades_{datetime.now().date().isoformat()}.json？[Y]: ").strip().upper()
                    if confirm == "Y":
                        ensure_weekly_backup(force=True)
                else:
                    if choice in symbols:
                        main_menu(choice)
                    else:
                        print("无效的输入")
            else:
                print("\n暂无品种记录")
                symbol = input("请输入品种名称[q退出, i导入交割单, h持仓历史]: ").strip().upper()
                if symbol.lower() == "q":
                    # print("再见！")
                    break
                elif symbol.lower() == "i":
                    # 导入交割单（不指定品种）
                    interactive_import_statement()
                elif symbol.lower() == "h":
                    # 持仓仓位历史分析
                    position_history_interactive()
                elif symbol:
                    main_menu(symbol)
        except KeyboardInterrupt:
            # print("\n\n操作已取消")
            return
            


if __name__ == "__main__":
    main()