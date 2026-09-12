# sse-tool-detail Design

## Context

`SSEStatusEvent` 有 4 字段：`stage` / `message` / `detail`（可选，仅非空序列化）/ `type`。`to_sse` / `from_payload` / `payload_for_buffer` 已全链路透传 detail——streaming-decouple 实现 9 事件序列化契约时 detail 就在。缺的只是产出端 `agent_service._convert_event` 在 tool start/end 时只填了固定 message，没填 detail。

```
现状:
  on_tool_start retrieve_kb → SSEStatusEvent(stage=retrieve, message="正在检索相关文档...", detail=None)
  on_tool_start search_web  → SSEStatusEvent(stage=web_search, message="正在联网搜索...", detail=None)
目标:
  ... detail="retrieve_kb query=腾讯2024年报 业绩 top_k=8"
  ... detail="search_web queries=['腾讯2024年报 业绩']"
```

## Goals / Non-Goals

**Goals:**
- 前端等待时能看到具体工具 + 检索/搜索对象
- 为多 Agent 留事件承载（detail 可扩展为 agent_name + tool 结构）
- buffer/resume 回放天然支持（不破坏 streaming-decouple 契约）

**Non-Goals:**
- 不改事件类型体系（不新增 SSEToolDetailEvent——detail 已够用）
- 不做完整工具调用参数回显（只展示人可读要点，不暴露全量 args）
- 不改后端任何流程逻辑（纯展示增强）

## Decisions

### D1: 复用 `SSEStatusEvent.detail`，不新增事件类

detail 字段 3 处契约已就位（payload_for_buffer / to_sse / from_payload），新增事件类反而要改 9 事件序列化分发 + buffer round-trip 测试，纯属浪费。填 detail 是唯一改动点。

### D2: detail 内容 = 工具名 + 入参要点（人可读）

```
retrieve_kb:  detail = f"query={input['query']}" (+ top_k 非默认时带)
search_web:   detail = f"queries={input['queries']}"
其他工具:     detail = None（不展示）
```

构造点：`_convert_event` 的 TOOL_START 分支（agent_service.py:205-221）。需从 LangGraph 事件 `data.input` 读工具入参（on_tool_start 的 input dict）。

### D3: 前端展示 = 状态文案 + detail 拼接

chat.html 的 status 渲染处：`detail` 存在时拼到 message 后（或独立小字），保持现有 stage 逻辑不变。

## 两条路线（态 A / 态 B）对照

本 change 是**纯展示层，两态通用**，detail 内容随实际工具调用自然区分：

| 环节 | 态 A（未选 KB，纯对话） | 态 B（选 KB，RAG） |
|---|---|---|
| 会显示的工具 | search_web（联网是主路径） | retrieve_kb（"正在检索: query=..."）+ search_web |
| detail 来源 | search_web 入参 queries | retrieve_kb 入参 query + search_web 入参 |
| 语义 | 展示"在搜什么"（正常） | 展示"在检/在搜什么"（含 KB 检索对象） |
| 事件兼容 | 同一 SSEStatusEvent | 同一 SSEStatusEvent |

无两态差异——detail 只是把"工具在做什么"透明化，不做任何质量/语义判断。

## Risks / Trade-offs

- [detail 可能含过长 query 撑爆 status 行] → 构造时截断（如 query[:40]，与日志截断一致）
- [前端渲染细节回归] → playwright 验证 status 区显示；无 detail 时走原逻辑（向后兼容）
- [对多 Agent 的预留] → detail 未来可升级为结构化 {agent, tool, query}，本次先字符串形态，不改契约
