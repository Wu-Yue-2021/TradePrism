# DeepSeek Anthropic 全对话 Agent 示例指南

> 研究日期：2026-08-23 ｜ 研究对象：DeepSeek 官方 Anthropic 兼容端点 + DSH（DeepSeek Harness）web_search 实现
> 结论先行：**整套程序统一使用 Anthropic Messages 协议**（`https://api.deepseek.com/anthropic/v1/messages`），主对话、function calling、网页搜索全部一个端点完成。

---

## 1. 结论速览

| 问题 | 答案 |
|---|---|
| 用什么协议 | **Anthropic Messages API**（不是 OpenAI chat completions，也不是 OpenAI Responses API） |
| 端点 | `POST https://api.deepseek.com/anthropic/v1/messages`（base_url = `https://api.deepseek.com/anthropic`） |
| 模型名 | `deepseek-v4-flash`、`deepseek-v4-pro` 均可直接传；`claude-opus-*`→pro、`claude-haiku/sonnet-*`→flash 自动映射；未知名称自动映射到 `deepseek-v4-flash` |
| 长对话 | ✅ 支持：多轮 `messages` 数组、`system`、`stream`、`thinking`、`tool_use/tool_result` 全部可用；上下文窗口 1M（不同模型以官方为准） |
| 网页搜索 | ✅ `web_search_20250305` server tool（DeepSeek 原生搜索，**仅此端点暴露**） |
| 图片输入 / MCP / 文档 | ❌ 该端点不支持（`image`、`document`、`mcp_tool_use` 均 Not Supported） |
| 成本 | ⚠️ 一次搜索 = 一次完整模型调用（延迟 + tokens），无独立检索端点 |

---

## 2. 背景：DSH 的 web_search 是怎么工作的

DSH（DeepSeek Harness）的 web 搜索是 **3 层链路**，全部已通过其包源码 README 核实：

```
主对话模型（普通 chat 模式）
   └─ tool_use: web_search(queries)                    # 第 1 层：模型只声明意图（function calling）
        └─ DSH 服务端执行器 dsh-tool-web                # 第 2 层：纯函数，负责 schema/格式/截断
             └─ ctx.web.search() → provider 解析
                  └─ 第 3 层：发起一次【全新的、独立的】
                     POST https://api.deepseek.com/anthropic/v1/messages
                     tools: [{ "type": "web_search_20250305" }]
                     → DeepSeek 服务器端真实执行搜索
                     → 返回结构化 web_search_tool_result 块
```

要点：
- **搜索不是模型的"能力"**：主模型只会调用工具，真正搜索是 DSH 服务端发的第二次 API 调用。
- **一次搜索 = 一次完整模型调用**：独立计费（DSH 配置 `maxTokens: 4096`、`maxUses: 5`）。
- **结果可靠**：返回结构化块（URL/标题/日期/引用片段），DSH 严格解析，**绝不从模型输出文字里抓取 URL**。
- **DSH 为什么双端点**：主对话走 OpenAI 兼容的 chat completions 端点（因为要支持视觉模型图片输入），仅搜索走 Anthropic 端点。**本指南的选型是统一走 Anthropic 端点**——若你不需要图片输入，一个 URL 全搞定，这是与 DSH 的差异点。

---

## 3. DeepSeek Anthropic 端点能力矩阵

来源：官方文档《Using the Anthropic API》（api-docs.deepseek.com/guides/anthropic_api）。

### 认证与请求

| 字段 | 状态 |
|---|---|
| `x-api-key` | ✅ 完整支持（**用这个，不是 Bearer**） |
| `anthropic-version` / `anthropic-beta` 头 | 忽略（可省略） |
| `model` | ✅ 用 DeepSeek 模型名 |
| `max_tokens` / `system` / `stream` / `stop_sequences` / `temperature` / `top_p` | ✅ 完整支持 |
| `metadata.user_id` | ✅ 支持（限流隔离用） |
| `thinking` | ✅ 支持（`budget_tokens` 忽略） |
| `output_config.effort` | ✅ 支持 |
| `top_k` / `container` / `service_tier` / `mcp_servers` | 忽略 |

### 工具

| 字段 | 状态 |
|---|---|
| 函数工具 `{name, input_schema, description}` | ✅ 完整支持 |
| `tool_choice`（none/auto/any/tool） | ✅ 支持 |
| server tool `{"type": "web_search_20250305"}` | ✅ 支持（搜索） |
| `cache_control` | 忽略（DeepSeek 服务端自动缓存前缀，无需手动标记） |

### 消息 content blocks

| 类型 | 状态 |
|---|---|
| `text` | ✅ |
| `tool_use` / `tool_result` | ✅（回传靠 `tool_use_id`） |
| `thinking` | ✅ |
| `server_tool_use` / `web_search_tool_result` | ✅（搜索结果的载体） |
| `image` / `document` / `search_result` | ❌ 不支持 |
| `redacted_thinking` / `mcp_tool_use` / `mcp_tool_result` / `code_execution_tool_result` / `container_upload` | ❌ 不支持 |

---

## 4. 环境准备

```bash
pip install anthropic
```

环境变量（二选一，推荐环境变量方式）：

```bash
export ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
export ANTHROPIC_API_KEY=${YOUR_DEEPSEEK_API_KEY}
```

或代码内显式传入（本指南示例统一用显式参数，便于阅读）：

```python
import os
from anthropic import Anthropic

client = Anthropic(
    base_url=os.environ.get("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic"),
    api_key=os.environ["DEEPSEEK_API_KEY"],
)
```

---

## 5. 示例代码

### 术语速查：三层易混淆名字

文档里出现三个"搜索"相关名字，各属不同层。**协议层名字是标准、不可改；代码层名字是本文档自选，已特意区分**：

| 名字 | 层 | 是什么 | 谁执行 |
|---|---|---|---|
| `tool_use` / `tool_result` | 协议 | 函数调用的请求/结果块类型（标准，不可改） | 你 |
| `web_search_20250305` | 协议 | 服务端搜索工具标识符（标准，不可改） | DeepSeek 服务器 |
| `web_search`（工具名） | 模型可见 | 函数工具定义 `{name, input_schema}`，模型发起 `tool_use` | 你 |
| `deepseek_search()` | 宿主内部 | 你的 Python 函数，向 DeepSeek 发起带 server tool 的请求 | 你（程序内部） |

调用链：模型发起 `web_search` 函数调用 → 你的程序执行它 → 内部调用 `deepseek_search()` → 该函数请求 DeepSeek 服务端工具 `web_search_20250305`。

以下示例从简到繁，均可独立运行。统一假设顶部已有：

```python
import os
from anthropic import Anthropic

client = Anthropic(
    base_url="https://api.deepseek.com/anthropic",
    api_key=os.environ["DEEPSEEK_API_KEY"],
)
MODEL = "deepseek-v4-flash"   # 或 "deepseek-v4-pro"
```

### 5.1 最小对话

```python
# 非流式
resp = client.messages.create(
    model=MODEL,
    max_tokens=1024,
    messages=[{"role": "user", "content": "你好，用一句话介绍你自己。"}],
)
print(resp.content[0].text)
```

```python
# 流式（长回复推荐，体验更好、还能早看到首字）
with client.messages.stream(
    model=MODEL,
    max_tokens=1024,
    messages=[{"role": "user", "content": "写一段 200 字左右的科普。"}],
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
```

### 5.2 长对话（多轮历史管理）

Anthropic 端点支持任意多轮 `messages` 数组；**没有自动压缩**（与 Claude 官方行为一致），长时间会话需自己管理。DeepSeek 上下文窗口 1M，普通应用很难撑爆，但给出一个安全策略：

```python
def chat_loop(history: list[dict], max_turns: int = 20):
    """简单长对话：保留最近 max_turns 轮，更早的历史用摘要代替。"""
    while True:
        user = input("你: ")
        if user.strip() in {"exit", "quit"}:
            break
        history.append({"role": "user", "content": user})

        # 简单压缩：只保留最近 max_turns 轮（真实场景可换成 LLM 摘要）
        trimmed = history[-max_turns * 2:]
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system="你是一个乐于助人的中文助手。",
            messages=trimmed,
        )
        reply = resp.content[0].text
        print("AI:", reply)
        history.append({"role": "assistant", "content": reply})

chat_loop([])
```

> 提示：`system` 单独传，不要塞进 messages；DeepSeek 服务端会自动做前缀缓存（实测 DSH 缓存命中率 99.7%），`system` 开头的前缀稳定时成本最低。

### 5.3 函数调用（function calling）

Anthropic 协议的工具定义：`{name, description, input_schema}`；模型返回 `tool_use` block；程序执行后回传 `tool_result`（靠 `tool_use_id` 关联）。

```python
def call_with_tool():
    # 1) 定义工具
    tools = [
        {
            "name": "get_weather",
            "description": "查询指定城市的当前天气。",
            "input_schema": {
                "type": "object",
                "properties": {"city": {"type": "string", "description": "城市名，如 北京"}},
                "required": ["city"],
            },
        }
    ]

    # 2) 发起对话，模型可能返回 tool_use
    resp = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        messages=[{"role": "user", "content": "北京现在天气怎么样？"}],
        tools=tools,
    )

    # 3) 执行工具并回传结果
    content = resp.content
    for block in content:
        if block.type == "tool_use":
            tool_result = query_weather(block.input["city"])  # 你的真实实现
            content.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": str(tool_result),
            })

    # 4) 带着工具结果再问一次，得到最终回答
    resp2 = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        messages=[
            {"role": "user", "content": "北京现在天气怎么样？"},
            {"role": "assistant", "content": content},   # 上一条 assistant 完整 content blocks
        ],
        tools=tools,
    )
    return resp2.content[0].text

def query_weather(city: str) -> str:
    return f"{city}：晴，25℃"   # 示例：替换为真实气象 API
```

### 5.4 网页搜索（server tool）

与函数调用不同，`web_search_20250305` 是 **server tool**：搜索由 DeepSeek 服务器端执行并返回结构化结果块，你的程序只是发起一次带该工具的普通 Messages 调用。

```python
def deepseek_search(query: str, max_tokens: int = 4096) -> list[dict]:
    """宿主内部函数：向 DeepSeek 服务端发起单次搜索（带 server tool）。

    这是 DSH dsh-web-search-deepseek 的复刻：一次搜索 = 一次完整模型调用，
    输入只有一行 query 文本，不携带任何对话历史。
    """
    resp = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[
            {"role": "user", "content": f"Perform a web search for the query: {query}"}
        ],
        tools=[{"type": "web_search_20250305"}],
    )
    results = []
    for block in resp.content:
        if block.type == "web_search_tool_result":
            # DeepSeek 返回结构：块内 content 再嵌套 web_search_result / text(带 citations)
            results.extend(extract_web_results(block))
    if not results:
        # 严格模式：搜索未触发时直接报错，而不是从模型文字里猜（DSH 同款策略）
        raise RuntimeError(f"该查询未返回 web_search_tool_result 块，搜索可能未触发: {query}")
    return results

def extract_web_results(block) -> list[dict]:
    """容错解析 web_search_tool_result 块。

    按 DeepSeek 实际返回解析：web_search_result 子块提供 url/title/page_age，
    引用片段位于 text 子块的 citations[].cited_text；字段缺失时跳过而不是抛错。
    """
    import json
    out = []
    raw = block.model_dump()  # 转 dict 便于遍历，避免依赖子块类型名
    for sub in raw.get("content", []):
        if sub.get("type") == "web_search_result":
            out.append({
                "url": sub.get("url", ""),
                "title": sub.get("title", ""),
                "page_age": sub.get("page_age"),
                "snippet": "",
            })
        elif sub.get("type") == "text":
            for cite in sub.get("citations", []) or []:
                if cite.get("url") and cite.get("cited_text"):
                    for item in out:
                        if item["url"] == cite["url"]:
                            item["snippet"] = cite["cited_text"]
    return out

# 使用
for r in deepseek_search("DeepSeek Harness 四种模式"):
    print(f"- {r['title']} | {r['url']} | {r['snippet'][:80]}")
```

> 注意：上例中的解析使用的是容错式遍历（`model_dump()` 后按字段名查找），因为 DeepSeek 响应结构的精确嵌套层级未经本机抓包实测；真实开发时建议先打印 `block.model_dump()` 确认结构。

### 5.5 完整 Agent 循环（复刻 DSH：提问 → 搜索 → 总结引用）

这是与 DSH 完全一致的架构：**主对话**用 function calling 声明搜索意图，**搜索**走独立的 server tool 调用，结果以 `tool_result` 回传主对话，由主模型总结并引用来源。

```python
# 模型可见的函数工具：name 用 "web_search"（对模型语义清晰）。
# 命名区分：宿主内部实现叫 deepseek_search()，协议层 server tool 是 web_search_20250305。
SEARCH_TOOL = {
    "name": "web_search",
    "description": "搜索互联网获取最新信息，返回若干来源 URL 与摘要。",
    "input_schema": {
        "type": "object",
        "properties": {"queries": {"type": "array", "items": {"type": "string"}}},
        "required": ["queries"],
    },
}

def agent_answer(question: str, max_depth: int = 3) -> str:
    """一次完整 agent 回合：模型可能多次发起搜索，直到给出最终回答。"""
    messages = [{"role": "user", "content": question}]
    for _ in range(max_depth):
        resp = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            messages=messages,
            tools=[SEARCH_TOOL],
        )
        blocks = resp.content

        tool_calls = [b for b in blocks if b.type == "tool_use"]
        if not tool_calls:
            # 模型直接回答（无工具调用）
            return "".join(b.text for b in blocks if b.type == "text")

        # 执行所有搜索（可并行，示例为串行）
        new_content = blocks
        for call in tool_calls:
            hits = []
            for q in call.input.get("queries", [])[:4]:   # 与 DSH 一致：每轮最多 4 个 query
                hits.extend(deepseek_search(q))           # 宿主内部实现，见 5.4
            new_content.append({
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": format_hits(hits),
            })
        messages.append({"role": "assistant", "content": new_content})
    return "（已达最大搜索轮次，未生成最终回答）"

def format_hits(hits: list[dict]) -> str:
    lines = [f"[{i+1}] {h['title']} - {h['url']}" + (f" | {h['snippet'][:120]}" if h.get("snippet") else "")
             for i, h in enumerate(hits)]
    return "\n".join(lines) if lines else "无搜索结果"

if __name__ == "__main__":
    print(agent_answer("DeepSeek Harness 的四种模式分别是什么？"))
```

**更简的替代（不推荐复刻 DSH 时用，但更贴近 Anthropic 原生生态）**：直接在主对话 `tools` 里放 `{"type": "web_search_20250305"}`（server tool），模型会自行触发搜索并自动把 `web_search_tool_result` 带回对话。代价是工具开关与搜索行为不透明、结果控制粒度低，DSH 正是为此才拆成两层。

---

## 6. 边界与注意事项

1. **每次搜索独立计费**：一次 `deepseek_search()` = 一次完整模型调用（DSH 配置 `max_tokens=4096`、`max_uses=5`）。高频 agent 场景注意成本；无结果上限，返回多时按需截断。
2. **严格模式**：响应无 `web_search_tool_result` 块时应抛错，不要从模型输出文字里抓 URL（DSH 同款策略，见 5.4）。
3. **长对话无自动压缩**：需要自己实现（截断 / 摘要 / 分层记忆）；1M 窗口下普通应用压力很小。
4. **不需要 `cache_control`**：DeepSeek 服务端自动前缀缓存（DSH 实测命中率 99.7%），保持前缀稳定（system 与工具定义不变）即可省钱。
5. **模型名**：推荐直接写 `deepseek-v4-flash` / `deepseek-v4-pro`；`claude-*` 名称会自动映射，未知名称退回 `deepseek-v4-flash`。
6. **视觉模型注意**：`deepseek-v4-flash-vision-exp` 可作 chat 模型使用，但该端点 **`image` 块不支持**——需要图片输入必须改走 chat completions 端点（DSH 主对话即如此）。
7. **`thinking` 支持但 `budget_tokens` 忽略**；推理力度受 `output_config.effort` 控制。

---

## 7. 参考链接

- DeepSeek 官方《Using the Anthropic API》：https://api-docs.deepseek.com/guides/anthropic_api/
- Anthropic SDK (Python)：https://github.com/anthropics/anthropic-sdk-python
- DSH 源码（web 搜索 provider README，本指南核心依据）：https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/master/packages/web/web-search-deepseek/README.md
- Anthropic 官方 Messages API 参考：https://platform.claude.com/docs/en/api/python/beta/messages/create
- 社区参考（协议转换的事实依据）：
  - claude-code-router 关于 server tools 转换的 issue：https://github.com/musistudio/claude-code-router/issues/1419
  - dsv4-cc-proxy-tray（让 Claude Code 跑在 DeepSeek V4 上）：https://github.com/Friend-Xu/dsv4-cc-proxy-tray
- DeepSeek V4 Flash Vision 模型信息：https://llm-stats.com/models/deepseek-v4-flash-vision-exp
