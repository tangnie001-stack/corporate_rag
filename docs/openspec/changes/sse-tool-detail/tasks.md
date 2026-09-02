# sse-tool-detail Tasks

## 1. 后端产出端填 detail

- [ ] 1.1 `agent_service._convert_event` TOOL_START 分支：从 LangGraph 事件 `data.input` 读工具入参，retrieve_kb 填 `detail=f"query={query}"`（top_k 非默认补）、search_web 填 `detail=f"queries={queries}"`，query 截断 40
- [ ] 1.2 `SSEInteractionTexts` 补工具明细相关文案/常量（如需）
- [ ] 1.3 单测：`test_dual_stream.py` / `test_agent_service.py` 断言 detail 内容；buffer round-trip（from_payload）含 detail 不回退

## 2. 前端展示

- [ ] 2.1 chat.html status 渲染：detail 存在时拼到 message 后（小字/独立样式），无 detail 走原逻辑
- [ ] 2.2 playwright 验证：检索中/联网中界面显示 query；无 detail 状态不回归

## 3. 质量门禁

- [ ] 3.1 `pytest tests/ -v` 全量通过；ruff / pyright 无新增 error
- [ ] 3.2 端到端：跑一条绑 KB query，SSE 流里 status 事件带 detail；刷新后 resume 回放 detail 仍在
- [ ] 3.3 api_contract.md 补 SSEStatusEvent.detail 说明（历史回放/前端对接用）
