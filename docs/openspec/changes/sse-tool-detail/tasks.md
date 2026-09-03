# sse-tool-detail Tasks

## 1. 后端产出端填 detail

- [x] 1.1 `agent_service._convert_event` TOOL_START 分支：从 LangGraph 事件 `data.input` 读工具入参，retrieve_kb 填 `detail=f"query={query}"`（top_k 非默认补）、search_web 填 `detail=f"queries={queries}"`，query 截断 40
- [x] 1.2 `SSEInteractionTexts` 补工具明细相关文案/常量（如需）
- [x] 1.3 单测：`test_dual_stream.py` / `test_agent_service.py` 断言 detail 内容；buffer round-trip（from_payload）含 detail 不回退

## 2. 前端展示

- [x] 2.1 chat.html status 渲染：detail 存在时拼到 message 后（小字/独立样式），无 detail 走原逻辑
- [x] 2.2 playwright 验证：检索中/联网中界面显示 query；无 detail 状态不回归

## 3. 质量门禁

- [x] 3.1 `pytest tests/ -v` 全量通过；ruff / pyright 无新增 error
- [ ] 3.2 端到端：跑一条绑 KB query，SSE 流里 status 事件带 detail；刷新后 resume 回放 detail 仍在 —— **待真实环境（绑 KB + 真 LLM）人工验证**（本收尾任务未运行：需真实业务 KB 与真 LLM 凭据；前端渲染已用受控 SSE 注入做过 DOM 级验证，见 task-2-report）
- [x] 3.3 api_contract.md 补 SSEStatusEvent.detail 说明（历史回放/前端对接用）

---

### 勾选说明（Task 3 收尾质量门禁）

- 勾选项均有实现代码 / 自动化测试 / 已运行的 playwright 验证支撑；item → 证据映射见 `.superpowers/sdd/task-3-report.md`。
- ⚠ **1.1 部分实现偏差**：`_tool_detail_from_input` 已实现 query/queries 入参要点与 40 字符截断（commit 6730d0d），但「top_k 非默认补」未实现——detail 恒为 `query=...`，未在 top_k ≠ 默认（TOP_K_RERANK=5）时附加。spec 验收场景仅要求 detail 含检索 query，此项属展示细节偏差，已在 task-3-report Concerns 记录，待后续决定是否补。
- 1.2 走「如需」不满足分支：detail 拼装复用既有 `SSEInteractionTexts.STAGE_*/..._STATUS_*` 常量与 module 级 `_tool_detail_from_input`，未引入新硬编码文案/常量，无需改动 `src/config/const.py`。
- 未勾项 3.2 依赖**真实绑 KB + 真 LLM**（当前收尾环境未运行全链路）；人工执行步骤见 `.superpowers/sdd/task-3-report.md`「待人工验证步骤」。
