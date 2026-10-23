# DeepSeek 网页搜索"判断循环"：四形态 × 两路径

> 研究日期：2026-10-19 ｜ 配套文档：`deepseek-anthropic-agent.md`（下称"主文档"，写于 2026-08-23）
> 主文档"无独立检索端点"的核心结论已被官方 2026-10-17 新 API 推翻。本文先记录已核实事实，再讨论"搜索结果由谁判断"的架构选型。

---

## 1. 已核实事实

| 事实 | 来源 | 可信度 |
|---|---|---|
| 独立 Search/Fetch API 上线（不经过模型）：`POST https://api.deepseek.com/v1/tools/search`、`POST .../v1/tools/fetch`；`Authorization: Bearer <key>` + `Content-Type: application/json` | 官方 search 指南 | 官方文档确认（主会话 curl 直连 + research-low 直连双重复核） |
| 按**成功请求次数**计费：各 ¥5/千次；失败、空结果、超时不计费；`count` 不影响单价 | 同上 | 官方文档确认 |
| QPS：Search/Fetch 各 5，账号级，与模型并发独立；Fetch 另有目标域名级 QPS；超限 429 | 同上 | 官方文档确认 |
| Search 请求字段：`query`(必填,1–128)、`count`(1–50,默认 10)、`ori_question`、`localization`、`need_content`；响应 `results[]`：`rerank_score`(0–3,≥1.5 优质)、`title`、`url`、`snippet`(100–200,给人看)、`summary`(600–800,给模型看)、`content`、`site_name`、`site_icon`、`date`(UTC RFC3339)、`authority`(0–4,≥3 极权威) | 同上 | 官方文档确认 |
| Search 语法操作符：`site:` / `-site:` / `-关键词` / `"精确短语"` / `lang:` / `after:` / `before:` | 同上 | 官方文档确认 |
| Fetch：请求 `url`(必填,≤8192) → 响应 `title` + `content`(Markdown)；错误为 OpenAI 兼容格式；408/429/5xx 可重试 | 同上 | 官方文档确认 |
| `web_search_20250305` server tool 仍受 Anthropic 端点支持；官方原文：DeepSeek Harness/Claude Code/Codex 经服务端工具访问 Search/Fetch 的调用**当前未计费，2026-11-01 00:00（北京时间）起按次收费** | 两官方页 | 官方文档确认 |
| Anthropic 端点 `image` 块现已支持：`source.type` 可为 base64(jpeg/png/gif/webp)/url/file，file 形式须带 `anthropic-beta: files-api-2025-04-14`；`claude*` 前缀映射不变（`claude-opus*`→`deepseek-v4-pro`，其余→`deepseek-flash`），**非 `claude` 开头的未知模型名返回 400**；官方示例已改用 `deepseek-flash` 命名 | anthropic_api 指南 | 官方文档确认（经中转镜像抓取，三次逐字一致；直连复核待做） |

---

## 2. 问题定义：搜索结果由谁判断

搜索原始结果不经判断直接进主对话的代价：上下文污染、后续每轮重复付费、前缀缓存失效。"结果是否可用/要不要再搜"的**终判必须有模型参与**（规则只能粗筛），承担这个判断循环的形态有四种：

| 形态 | 判断发生地 | 额外 LLM 成本 | 上下文隔离 | 成立前提 |
|---|---|---|---|---|
| **A 主循环即判断** | 主模型下一轮回复 | 0 | 无（仅靠机械截断兜底） | tool_result 已截干净 |
| **B 无状态单发** | 一次小模型调用，无历史不追问 | 每条目 1 次小调用 | 正文不进主上下文 | 全文长、筛选标准简单 |
| **C 常驻子 agent** | 独立上下文多轮循环，只回报告 | 一整个 agent | 完全隔离（笔记本随其销毁） | 多来源交叉/迭代求证 |
| **D 纯规则** | 不判断，只筛 | 0 | — | API 自带分数字段（只有路径二满足） |

---

## 3. 形态 × 路径实现矩阵

- **路径一** = 主文档 §5.4/§5.5：messages 端点 + server tool 二次调用，解析 `web_search_tool_result` 块。
- **路径二** = 直连新 JSON API：`requests.post` 打 `/v1/tools/search|fetch`，吃结构化字段。

| 形态 | 路径一（server tool） | 路径二（Search/Fetch API） |
|---|---|---|
| D | 做不了——返回块无分数/权威度字段 | **首次成为一等公民**：`rerank_score≥1.5` ∧ `authority≥3` ∧ `date` 窗口，零 token 初筛 |
| A | 主文档现状：`format_hits` 截断 + 主模型 `max_depth` 循环；但一次搜索本身=一次模型调用，成本下限高 | **最佳默认**：宿主函数直连，`tool_result` 只喂 top-N × `summary`（该字段 600–800 字符、专为喂模型设计） |
| B | 材料本身就是模型输出，再套摘要性价比差 | **标准组合**：Fetch 拿 Markdown，超阈值（如 8KB）时一次 flash 单发抽取"与 `ori_question` 相关段"——即 Claude Code WebFetch 的同款设计 |
| C | 可行，但子 agent 内每次搜索仍是模型调用，双重浪费 | 恰适用：多来源交叉、产出报告；子 agent 内部直吃 JSON，主对话只收结论 |

路径二 A+D 骨架：

```python
import requests

def web_search_impl(query: str, ori_question: str, top_n: int = 5) -> str:
    resp = requests.post(
        "https://api.deepseek.com/v1/tools/search",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={"query": query, "ori_question": ori_question,
              "count": top_n * 2, "need_content": False},
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json()["results"]
    keep = [r for r in results if r["rerank_score"] >= 1.5] or results[:1]
    return "\n".join(
        f"[{i+1}] {r['title']} | {r['url']} | authority={r['authority']} | {r['summary']}"
        for i, r in enumerate(keep[:top_n])
    )
```

路径二 B 骨架（对单条结果深读）：

```python
def web_fetch_impl(url: str, ori_question: str, raw_limit: int = 8000) -> str:
    page = requests.post(
        "https://api.deepseek.com/v1/tools/fetch",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={"url": url}, timeout=15,
    ).json()["content"]
    if len(page) <= raw_limit:
        return page
    # 超限才花一次小模型单发；无历史、不追问
    return extract_relevant_once(FLASH_MODEL, page, ori_question)
```

---

## 4. 选型与成本

| 任务类型 | 推荐组合 |
|---|---|
| 查事实（一问一答） | 路径二 A+D，一轮闭环 |
| 长文/单问题深读 | 路径二 A+D，按需加 B |
| 对照研究、产出报告 | 路径二 C（内部自含 A/B/D） |
| 硬要求零宿主代码 | 路径一 server tool 直挂——但 2026-11-01 计费开关落地后此路径失去成本优势，新实现不建议 |

量级参考（以官方定价页为准）：路径二 5 次搜索 ≈ ¥0.025 且上下文只进摘要；路径一 5 次搜索 ≈ 5 次 4096-token 模型调用。上下文增量对比：A ≈ 搜索次数 × 摘要长；C ≈ 报告长（工作笔记全隔离）。

---

## 5. 主文档修订清单

| # | 主文档说法（概念定位） | 官方现状 | 处理 |
|---|---|---|---|
| D1 | "无独立检索端点"（§1 成本行、§2、§6.1） | 独立 REST 端点已存在 | 改写 |
| D2 | "网页搜索仅此端点（Anthropic messages）暴露"（§1） | `/v1/tools/search` 直连可搜 | 改写 |
| D3 | "一次搜索=一次完整模型调用，tokens+延迟"（§1、§2、§6.1） | 独立 API 按次计费 ¥5/千 | 改写 |
| D4 | "image ❌，需图片必须走 chat completions"（§1、§3、§6.6） | image 已支持（file 形式带 beta 头） | 改写 |
| D5 | "未知模型名退回 flash"（§1、§6.5） | 非 `claude*` 前缀 → 400 | 改写 |
| D6 | `deepseek-v4-flash` 为默认名（§1、§5） | 官方示例/映射目标为 `deepseek-flash` | 复核命名 |
| D10 | 无 server tool 计费时间点 | 官方：2026-11-01 00:00 起按次收费 | 补写 |
| — | §2 DSH 三层链路 | 新路径下塌缩为两层（§3） | 标注为 2026-08-23 时点快照 |

未决（如实标注）：

1. anthropic_api 页证据本轮仅经镜像抓取（内容三次逐字一致），待直连复核。
2. `cache_control` 忽略/自动前缀缓存（含 DSH 99.7% 命中率）本轮两官方页未覆盖，未证实未证伪。
3. server tool 路径的计费单位是否等于一次 Search 调用，官方未逐字定义。
4. DSH 源码 README（主文档 §2 核心依据）未核对最新 master。

---

## 6. 核验来源

本文 §1–§3 事实由双通道独立核验：主会话 `curl.exe` 直连抓取三份官方页逐字核对 + `research-low` 子 agent 按其输出契约交叉核查（样本口径：本地主文档 1 份全文精读、官方页 5 次抓取）。参考：

- 网页搜索与抓取指南：https://api-docs.deepseek.com/zh-cn/guides/search/
- Anthropic API 兼容指南：https://api-docs.deepseek.com/zh-cn/guides/anthropic_api/
- 发布公告（2026-10-17）：https://api-docs.deepseek.com/zh-cn/news/news261017