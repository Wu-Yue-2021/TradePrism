# 做T匹配算法讨论文档

## 1. 背景

`irr_analyzer.py` 当前用于识别做T交易、战略交易、当前持仓，并计算 IRR、ROI、ARR 等指标。

现有做T识别逻辑使用 `window_days` 滑动窗口：

```text
按时间顺序遍历交易记录
  清理超过 window_days 的旧记录
  当前交易立即尝试匹配窗口内已有的反向交易
  匹配成功则记为做T
  未匹配部分留在窗口队列
遍历结束后，窗口剩余交易进入战略持仓逻辑
```

这个逻辑简单，但它是**单向流式匹配**：当前交易只能看到过去交易，不能看到未来交易。因此在事后复盘中，可能不符合人工对“哪笔在做T”的直觉。

---

## 2. 触发问题的用例

东材科技交易片段：

```text
2026-05-12 14:27:55 SELL 100
2026-05-12 15:00:01 BUY  200
2026-05-13 14:03:58 SELL 100
2026-05-13 14:49:27 BUY  100
2026-05-14 10:57:15 BUY  100
2026-05-15 11:26:29 SELL 100
2026-05-15 13:55:40 BUY  100
```

当前算法得到的部分做T匹配：

```text
2026-05-15 11:26:29 SELL 100
匹配到
2026-05-13 14:49:27 BUY 100
```

用户期望更接近：

```text
2026-05-15 11:26:29 SELL 100
优先匹配
2026-05-15 13:55:40 BUY 100
```

原因：后者时间距离更近，且符合“先卖后买”的反T复盘语义。

---

## 3. 当前算法为什么会这样

当前 `_match_or_queue()` 是 FIFO 贪心匹配：

```text
当前交易到来
  从 window_queue 队首开始扫描
  找到第一条反向交易就匹配
```

因此在处理 `2026-05-15 11:26:29 SELL` 时，队列里已有：

```text
2026-05-13 14:49:27 BUY
2026-05-14 10:57:15 BUY
```

算法从队首开始找反向交易，于是先匹配 `2026-05-13 14:49:27 BUY`。

此时：

```text
2026-05-15 13:55:40 BUY
```

还没有被遍历到，所以当前算法不可能匹配它。

---

## 4. 已讨论方案与问题

### 4.1 方案一：只在报表增加数量列

目的：解释同一时间戳交易被拆分成多笔匹配时不是重复计入。

例如：

```text
2026-05-12 15:00:01 BUY 200
拆成：
  100 股匹配 2026-05-12 SELL
  100 股匹配 2026-05-13 SELL
```

优点：

```text
改动最小，只改善展示误解。
```

问题：

```text
不能解决 5.15 SELL 匹配到较早 BUY 的算法问题。
```

结论：

```text
不是根本解决方案。
```

---

### 4.2 方案二：最近过去交易优先

规则：

```text
当前交易只匹配过去窗口内最近的反向交易。
```

对问题用例：

```text
2026-05-15 11:26 SELL
会匹配
2026-05-14 10:57 BUY
```

优点：

```text
改动小，只需把 window_queue 扫描顺序从 FIFO 改成从后往前。
比当前 FIFO 更接近“最近一笔做T”的直觉。
```

问题：

```text
仍然只能看到过去，不能识别未来发生的反T。
因此不会匹配 2026-05-15 13:55 BUY。
```

结论：

```text
可作为低风险小修，但不满足事后复盘中“时间距离最近”的反T识别。
```

---

### 4.3 方案三：全局候选池，按时间距离最短匹配

规则：

```text
1. 先收集所有交易。
2. 生成所有 window_days 内的 BUY/SELL 候选组合。
3. 按时间距离从小到大排序。
4. 依次匹配，扣减 remaining_qty。
5. 做T消耗后的剩余交易再进入战略持仓逻辑。
```

优点：

```text
最符合事后复盘语义。
可以自然识别正T和反T。
可以避免早期交易抢走更近的未来反向交易。
```

问题：

```text
需要把 run() 改成两阶段：先识别做T，再处理战略持仓。
需要维护每条交易的 remaining_qty。
需要处理部分成交拆分。
需要保证做T现金流、战略现金流、当前持仓三者一致。
实现复杂度中等，超过当前 bug 修复范围。
```

结论：

```text
长期最完整，但目前可能过度设计。
方案七实现了此思路的优化版本：
用最小堆 + 惰性删除，避免了全排序的 O(N² log N) 复杂度。
```

---

### 4.4 方案四：三条记录 lookahead，处理中间记录

初始想法：

```text
每条记录先入队。
当队列长度 >= 3 时，处理第二条记录。
第二条记录同时能看到前一条和后一条。
匹配其中更近的反向交易。
```

示例：

```text
1. 2026-05-14 BUY
2. 2026-05-15 11:26 SELL
3. 2026-05-15 13:55 BUY
```

处理中间的 SELL 时：

```text
前面 BUY：正T候选
后面 BUY：反T候选
```

比较距离后，匹配后面的 BUY。

优点：

```text
保留流式结构。
只加一条未来记录的观察能力。
比全局候选池简单。
```

问题：连续同方向交易会失效。

反例：

```text
1. 2026-05-13 BUY
2. 2026-05-14 BUY
3. 2026-05-15 BUY
4. 2026-05-16 SELL
```

如果队列长度到 3 就处理第二条 `2026-05-14 BUY`：

```text
前后都是 BUY，无法匹配。
如果此时直接把它转入战略持仓，后面的 2026-05-16 SELL 就失去匹配机会。
```

结论：

```text
“队列长度 >= 3 就处理第二条”不够安全。
不能在未匹配时过早出队或转战略。
```

---

### 4.5 方案五：中心交易可决策时才匹配

这是目前讨论出的折中方案。

核心规则：

```text
不要单纯按队列长度触发匹配。
而是判断某条中心交易 center 是否已经“可决策”。
```

center 可决策条件：

```text
条件 A：center 前面存在最近的反向候选，后面也存在最近的反向候选。
       此时可以比较前后时间距离，选更近者匹配。

条件 B：center 已经超过 window_days，不可能再等到有效未来候选。
       此时用已有最近反向候选匹配；如果没有候选，则进入战略持仓。
```

伪代码：

```python
def should_resolve(center, queue, newest_ts):
    prev_opposite = find_nearest_opposite_before(center, queue)
    next_opposite = find_nearest_opposite_after(center, queue)

    has_both_sides = prev_opposite is not None and next_opposite is not None
    expired = (newest_ts.date() - center["ts"].date()).days > window_days

    return has_both_sides or expired
```

匹配决策：

```python
def choose_match(center, prev_opposite, next_opposite):
    if prev_opposite and next_opposite:
        prev_distance = abs(center["ts"] - prev_opposite["ts"])
        next_distance = abs(center["ts"] - next_opposite["ts"])
        return prev_opposite if prev_distance <= next_distance else next_opposite

    return prev_opposite or next_opposite
```

注意：这里的“前面最近反向候选”不是原始队列第一条，而是候选视图里的最近反向交易。

例如：

```text
原始队列：
1. 2026-05-13 BUY
2. 2026-05-14 BUY
3. 2026-05-15 11:26 SELL
4. 2026-05-15 13:55 BUY

对 center = 2026-05-15 11:26 SELL：
前方最近反向候选 = 2026-05-14 BUY
后方最近反向候选 = 2026-05-15 13:55 BUY
```

`2026-05-13 BUY` 没有被删除，只是本次候选选择中被 `2026-05-14 BUY` 覆盖。它仍然留在队列里，之后可能进入战略持仓或匹配后续 SELL。

优点：

```text
保留滑动窗口思想。
允许识别近距离反T。
不会因为连续同方向交易过早转战略。
复杂度低于全局候选池。
```

问题：

```text
已经接近局部状态机。
需要明确每条交易何时可以出队。
需要避免候选覆盖时丢失实际交易数量。
不是全局最优匹配，只是局部最短距离匹配。
```

结论：

```text
这是当前最合适的折中方向。
```

---

## 4.6 方案六：滑动窗口状态机（已实现于 code_filed/irr_analyzer_滑动窗口状态机版本.py）

核心思想：

```text
1. 全部交易读入 t_window 列表
2. 维护 prev_buy_idx/prev_sell_idx/next_buy_idx/next_sell_idx 四个游标
3. 以 center 方式遍历，每个 center 同时看前后反向候选
4. 选择时间更近的候选匹配
5. 若更近的同方向 center 存在于 center 和候选之间，让更近的 center 先配
6. 收尾阶段用栈匹配剩余窗口
7. 剩余交易进入战略持仓
```

关键数据结构：

```python
# WindowTrade - 窗口内交易
{
    “ts”: datetime,      # 交易时间
    “price”: float,      # 成交价格
    “qty”: int,          # 剩余数量（匹配会消耗）
    “side”: str,         # 'BUY' | 'SELL'
}

# TTrade - 做T记录
{
    “sell_ts”: str,
    “buy_ts”: str,
    “qty”: int,
    “buy_price”: float,
    “sell_price”: float,
    “days”: float,
    “pnl”: float,
    “investment”: float,
    “fee”: float,
    “rar”: str,
    “arr”: str,
    “t_type”: str,       # ' 正T' | ' 反T'
}
```

核心函数：

```python
def _scan_prev_opposite(from_idx, side) -> int | None:
    “””从 from_idx 向前扫描找最近的 side 交易”””

def _scan_next_opposite(from_idx, side) -> int | None:
    “””从 from_idx 向后扫描找最近的 side 交易”””

def _try_delayed_match(center_idx, prev_idx, next_idx) -> tuple[bool, int | None]:
    “””
    对 center 尝试延迟匹配：
    1. 检查候选是否在窗口内
    2. 选择时间更近的候选
    3. 检查是否有更近的同方向 center 应该优先匹配
    4. 返回 (是否匹配, target_idx)
       - target_idx > 0: 被消耗的候选索引
       - target_idx == -1: prev 被更近的 center 消耗，需重试
       - target_idx == -2: next 被更近的 center 消耗，需重试
    “””

def _flush_t_window_stack():
    “””收尾阶段用栈匹配剩余窗口”””
```

处理 SBSB 场景（SELL-BUY-SELL-BUY）：

```text
当 center = SELL[2]，next = BUY[4] 更近时：
1. 检查 SELL[3] 是否在 center 和 next 之间
2. 若 SELL[3] 存在，让 SELL[3] 先配 BUY[4]
3. 返回 -2，外层重试
```

优点：

```text
保留滑动窗口思想。
允许识别近距离反T。
不会因为连续同方向交易过早转战略。
复杂度低于全局候选池。
```

问题：

```text
游标管理复杂，容易被 None 值干扰。
多层嵌套逻辑难以调试。
closer_center 检查的范围边界容易出错。
当候选被消耗后游标更新逻辑复杂。
```

### 方案五优化成方案六过程中的讨论

方案六由方案五优化而来，以下是优化过程中需要明确的关键概念和规则。

#### "覆盖"的正确定义

连续同方向交易时，center 选择候选时会出现"覆盖"现象。

示例：

```text
2026-05-13 BUY 100
2026-05-14 BUY 100
2026-05-15 SELL 100
```

对 `2026-05-15 SELL` 来说：

```text
最近前向反向候选是 2026-05-14 BUY
2026-05-13 BUY 被覆盖
```

这里的覆盖只表示：

```text
2026-05-14 BUY 比 2026-05-13 BUY 更适合作为这次 SELL 的做T候选。
```

不能表示：

```text
删除 2026-05-13 BUY
丢弃 2026-05-13 BUY 的数量
提前把 2026-05-13 BUY 转战略
```

否则会导致持仓、盈亏、现金流错误。

---

#### 出队规则

在游标遍历过程中，一条交易从队列中移除只有三种原因：

```text
1. remaining_qty 被做T匹配消耗完。
2. 交易已经超过 window_days，且没有可匹配候选，需要进入战略持仓。
3. 交易已经超过 window_days，使用已有候选完成最后一次匹配，剩余部分再进入战略持仓。
```

不能出队的情况：

```text
只是暂时没有匹配到。
只是被更近的同方向交易覆盖为候选。
只是队列长度超过 3。
```

---

#### 遍历复杂度分析

**方案五：流式处理，每次入队触发遍历**

```text
每加入一条新交易：
  遍历队列中除首尾外的 center
  判断 center 是否可决策
  可决策则匹配或转战略
```

复杂度：

```text
每次入队：O(k)，k = 当前队列长度
总复杂度：O(n * k_avg)，n = 总交易数，k_avg = 平均队列长度
```

最坏情况（队列持续增长）：

```text
O(n²)
```

但实际场景下 window_days 限制了队列长度，k_avg 通常很小。

**方案六：离线处理，全部读入后遍历**

```text
全部交易读入 t_window
for center_idx in range(start_idx, len(t_window)):
    扫描 prev_opposite：O(k)
    扫描 next_opposite：O(k)
    匹配处理
```

复杂度：

```text
每个 center 扫描：O(k)，k = window_days 内平均交易数
总复杂度：O(n * k)
```

由于 window_days 固定，k 是常数级，实际复杂度接近 O(n)。

**对比**

```text
方案五：依赖队列长度，可能退化到 O(n²)
方案六：离线处理，复杂度稳定 O(n * k)
方案七：堆匹配，复杂度 O(m log n)，m = 实际匹配次数
```

---

## 4.7 方案七：动态堆匹配（最终实现方案）

核心思想：

```text
1. 将所有交易存入列表，用双向索引模拟链表
2. 初始扫描所有相邻反向对，按时间差放入最小堆
3. 循环弹出堆顶（时间差最小）
4. 惰性删除检查：验证是否仍为相邻节点、双方数量 > 0
5. 可选交叉检查：禁止/允许匹配对在时间轴上交错
6. 执行匹配，扣减数量，更新链表，新形成的相邻对入堆
7. 剩余交易进入战略持仓
```

算法步骤：

```python
def run():
    # 1. 构建双向索引链表
    for i, trade in enumerate(trades):
        trade['prev'] = i - 1 if i > 0 else None
        trade['next'] = i + 1 if i < len(trades) - 1 else None
    
    # 2. 初始扫描相邻反向对，入堆
    heap = []
    for i, trade in enumerate(trades):
        if trade['next'] and trades[trade['next']]['side'] != trade['side']:
            days = abs((trade['ts'] - trades[trade['next']]['ts']).days)
            if days <= window_days:
                dist = abs((trade['ts'] - trades[trade['next']]['ts']).total_seconds())
                heapq.heappush(heap, (dist, i, trade['next']))
    
    # 3. 循环弹出堆顶
    matched_pairs = []  # 用于交叉检查
    while heap:
        dist, i, j = heapq.heappop(heap)
        
        # 惰性删除检查
        if not is_adjacent(i, j) or trades[i]['qty'] <= 0 or trades[j]['qty'] <= 0:
            continue
        
        # 交叉检查（可选）
        if not allow_cross and is_crossing(i, j, matched_pairs):
            continue
        
        # 执行匹配
        match_qty = min(trades[i]['qty'], trades[j]['qty'])
        record_t_trade(i, j, match_qty)
        matched_pairs.append((i, j))
        
        # 更新链表，检查新的相邻对
        if trades[i]['qty'] == 0:
            remove_from_list(i)
            check_new_adjacent(trades[i]['prev'], trades[i]['next'])
        if trades[j]['qty'] == 0:
            remove_from_list(j)
            check_new_adjacent(trades[j]['prev'], trades[j]['next'])
    
    # 4. 剩余进入战略持仓
    for trade in trades:
        if trade['qty'] > 0:
            add_to_strategic_inventory(trade)
```

交叉检查逻辑：

```python
def is_crossing(b, s, matched_pairs):
    “””检查 (b, s) 是否与已匹配对产生交错”””
    mb, ms = min(b, s), max(b, s)
    for b2, s2 in matched_pairs:
        mb2, ms2 = min(b2, s2), max(b2, s2)
        # 交错条件：(mb < mb2 < ms < ms2) or (mb2 < mb < ms2 < ms)
        if (mb < mb2 < ms < ms2) or (mb2 < mb < ms2 < ms):
            return True
    return False
```

两种匹配模式：

```text
allow_cross=True:  允许交叉，任何相邻反向对均可匹配
allow_cross=False: 禁止交叉，保持严格时间顺序
```

优点：

```text
彻底解决 SBSB 交叉问题。
O(M log N) 复杂度，M 为实际匹配次数。
通过开关支持两种复盘视角。
逻辑清晰，无需复杂游标管理。
惰性删除避免频繁堆更新。
```

已解决的问题：

```text
1. SBSB 序列交叉匹配错误（B1 抢走 S2）
2. 无需全排序，动态堆方法高效
3. 可选择是否允许交叉
4. 算法逻辑清晰，无复杂游标补丁
```

仍存在的问题：

```text
1. 手续费拆单分摊未实施
   - 当前每笔小单独立计算手续费，拆单后总手续费虚高
   - 解决方案：按 match_qty / 原始总量 比例分摊

2. 战略持仓手续费未分摊
   - 部分做T、部分战略的交易手续费重复计算

3. 允许交叉模式的解释困难
   - 同一资金可能被”重复使用”，用户需理解这种逻辑
```

---

## 5. 历史优化方案与最终选择

本节记录讨论过程中考虑过的优化方案，以及最终为何选择方案七。

---

### 8.1 简单标志位（未采用）

每条队列记录维护：

```python
{
    "ts": ...,
    "side": "BUY" | "SELL",
    "remaining_qty": ...,
    "prev_opposite_index": None,
    "next_opposite_index": None,
    "resolved": False,
}
```

新记录入队后，只更新受影响的记录：

```text
如果新记录是 BUY，只可能成为前面 SELL 的 next_opposite。
如果新记录是 SELL，只可能成为前面 BUY 的 next_opposite。
```

这样不用每次全量扫描所有 center。

问题：

```text
匹配后某条记录 remaining_qty 变成 0 时，相关 index 可能失效，需要重建或局部修正。
```

未采用原因：

```text
方案六游标管理已经足够复杂，再加标志位维护更易出错。
方案七用惰性删除彻底绕过了索引失效问题。
```

---

### 8.2 双队列候选（未采用）

维护两个方向的队列：

```python
buy_queue
sell_queue
```

每条中心交易查找候选时：

```text
center 是 BUY：查 sell_queue 中时间在前后的最近 SELL
center 是 SELL：查 buy_queue 中时间在前后的最近 BUY
```

优点：

```text
查找反向候选更直接。
```

问题：

```text
交易被部分匹配后，需要同步更新主队列和方向队列。
窗口过期时也要同步删除。
状态一致性更难维护。
```

未采用原因：

```text
方案七用双向索引链表 + 单一列表，状态更简单。
```

---

### 8.3 优先队列（已融入方案七）

把候选匹配按时间距离放进优先队列：

```text
priority = abs(buy_ts - sell_ts)
```

优点：

```text
自然得到时间距离最短的候选。
```

问题：

```text
这会逐步接近全局候选池方案。
需要处理 stale candidate：候选中的交易可能已经被其他匹配消耗完。
需要每次弹出时检查 remaining_qty 是否仍然有效。
```

最终选择：

```text
方案七采用了这个思路，并用惰性删除解决 stale candidate 问题：
1. 弹出堆顶时检查双方是否仍相邻、数量是否仍 > 0
2. 若检查失败直接丢弃，继续弹下一个
3. 无需主动从堆中删除失效条目

这种方法比主动删除更简单，且总复杂度仍为 O(M log N)。
```

---

## 6. 最终实现顺序
### 第一版：滑动窗口状态机（方案六）

先不做复杂优化，最初尝试用游标管理实现延迟匹配，可以接受局部遍历：

```text
交易量很小，先保证正确性和可读性。
不要一开始引入优先队列、前缀索引或复杂缓存。
```

实现原则：

```text
1. 保留 window_days。
2. 每条交易先进入 queue。
3. 每次入队后，从队列中寻找可决策 center。
4. center 前后都有反向候选时，选时间距离更近者。
5. center 过期时，不再等待未来候选。
6. 做T消耗后更新 remaining_qty。
7. 被消耗完的交易从 queue 移除。
8. 过期且未匹配的交易进入战略持仓。
```

核心逻辑：
```text
1. 维护 prev_buy_idx/prev_sell_idx/next_buy_idx/next_sell_idx 四个游标
2. 以 center 方式遍历，每个 center 同时看前后反向候选
3. 选择时间更近的候选匹配
```

遇到的问题：

```text
1. 游标管理复杂，容易被 None 值干扰
2. closer_center 检查的范围边界容易出错
3. 当候选被消耗后游标更新逻辑复杂
4. SBSB 场景下 range 边界错误导致漏检
```

结论：

```text
游标方案虽然直观，但状态管理过于复杂。
一旦出现边界条件 bug，修复成本高且容易引入新问题。
```

### 第二版：动态堆匹配（方案七，最终实现）

当第一版遇到 SBSB 交叉问题或性能瓶颈时，升级为动态堆方法：

```text
1. 双向索引模拟链表
2. 最小堆管理候选对
3. 惰性删除避免频繁更新
4. 可选交叉检查
```

---

## 7. 关键测试用例

### 用例 1：同日反T优先

```text
BUY  2026-05-14 10:57 100
SELL 2026-05-15 11:26 100
BUY  2026-05-15 13:55 100
```

期望：

```text
SELL 2026-05-15 11:26
匹配
BUY  2026-05-15 13:55
类型：反T
```

---

### 用例 2：最近过去正T

```text
BUY  2026-05-13 14:49 100
BUY  2026-05-14 10:57 100
SELL 2026-05-15 11:26 100
```

期望：

```text
SELL 2026-05-15 11:26
匹配
BUY  2026-05-14 10:57
类型：正T
```

`2026-05-13 BUY` 保留，不丢失。

---

### 用例 3：连续同方向不应提前转战略

```text
BUY  2026-05-13 100
BUY  2026-05-14 100
BUY  2026-05-15 100
SELL 2026-05-16 100
```

期望：

```text
不能在第三条 BUY 到来时就把第二条 BUY 转战略。
SELL 到来后，应优先匹配最近的 BUY。
```

---

### 用例 4：部分成交拆分

```text
BUY  2026-05-12 15:00 200
SELL 2026-05-13 14:03 100
SELL 2026-05-14 14:03 100
```

期望：

```text
BUY 200 可以拆成两份 100 分别匹配。
同一笔交易可以出现多次，但总消耗不能超过 200。
报表最好显示 qty，避免误解为重复计入。
```

---

### 用例 5：窗口过期进入战略

```text
BUY  2026-05-01 100
SELL 2026-05-10 100
window_days = 3
```

期望：

```text
两者不能识别为做T。
BUY 过期后进入战略持仓。
SELL 后续按战略平仓逻辑处理。
```

---

### 用例 6：过期时使用已有候选

```text
BUY  2026-05-01 100
SELL 2026-05-03 100
之后没有更多记录
window_days = 3
```

期望：

```text
遍历结束清空队列时，仍应识别这组做T。
不能因为没有”后方候选”就漏掉已成立的正T。
```

---

### 用例 7：SBSB 交叉场景（方案六/七重点）

```text
SELL 2026-05-13 10:00 100
BUY  2026-05-13 11:30 100
SELL 2026-05-13 12:30 100
BUY  2026-05-13 13:00 100
```

期望：

```text
禁止交叉模式：
  SELL 10:00 匹配 BUY 11:30
  SELL 12:30 匹配 BUY 13:00

允许交叉模式：
  SELL 10:00 匹配 BUY 13:00（时间差最大）
  SELL 12:30 匹配 BUY 11:30（时间差最大）
  
动态堆方法（时间差最小优先）：
  SELL 12:30 匹配 BUY 13:00（相邻，时间差最小）
  SELL 10:00 匹配 BUY 11:30（相邻，时间差最小）
```

---

## 8. 当前结论

### 问题根源

当前 FIFO 滑动窗口的问题不是 `window_days` 本身，而是：

```text
交易一入队就立即匹配，导致它只能看到过去，不能看到未来。
```

### 最终实现方案

采用 **方案七：动态堆匹配**，核心特点：

```text
1. 双向索引模拟链表，O(1) 删除
2. 最小堆管理候选对，O(log N) 取最优
3. 惰性删除，避免频繁堆更新
4. 可选交叉检查，支持两种复盘视角
5. 总复杂度 O(M log N)，M 为实际匹配次数
```

### 已解决的问题

```text
1. SBSB 序列交叉匹配错误
2. 无需全排序，动态堆方法高效
3. 可选择是否允许交叉
4. 算法逻辑清晰，无复杂游标补丁
```

### 待解决的问题

```text
1. 手续费拆单分摊
2. 战略持仓手续费分摊
3. 允许交叉模式的解释说明
```