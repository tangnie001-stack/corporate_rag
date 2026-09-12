# sse-tool-detail Proposal

## Why

SSE 状态事件只发固定文案（"正在检索相关文档…" / "正在联网搜索…"），前端无法看到**具体在检索什么 / 搜什么 query**。用户等待时只知道"在检索"，不知道检索对象，调试与体验都欠缺。工具调用明细（"正在检索: 腾讯2024年报 业绩"）让等待过程可见，也为多 Agent 阶段（谁在调什么工具）留好事件承载。

`SSEStatusEvent` 已带可选 `detail` 字段（sse.py:21），且 to_sse / from_payload / payload_for_buffer 全链路已透传——**无需新增事件类型**，只需在产出端填 detail。

## What Changes

- `agent_service._convert_event`：tool start/end 事件发 `SSEStatusEvent` 时填充 `detail`——携带工具名 + 入参要点（retrieve_kb 带 query / top_k；search_web 带 queries 数组）
- `SSEInteractionTexts`：可选补工具明细相关文案（或直接构造 detail 字符串）
- 前端 chat.html：status 事件渲染时展示 detail（若存在），与现有状态文案拼接
- buffer/resume（streaming-decouple 已实现）天然兼容：detail 已进 payload_for_buffer，回放无需改

## Capabilities

### New Capabilities
- `sse-tool-detail`: SSE 状态事件携带工具调用明细（工具名 + query），纯展示增强

### Modified Capabilities
- （无 — 不改既有事件契约，SSEStatusEvent.detail 已存在，仅启用）

## Impact

- `src/services/agent_service.py` — `_convert_event` 的 TOOL_START/END 分支填 detail
- `src/config/const.py` — `SSEInteractionTexts` 可选补文案
- `deploy/nginx/html/chat.html` — status 渲染兼容 detail
- `src/utils/sse.py` — 无改动（detail 全链路已透传）
- 测试：`test_dual_stream.py` / `test_agent_service.py` 断言补 detail；buffer round-trip 已覆盖 detail
