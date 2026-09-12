# delegate-hardening-observability Proposal

## Why

fork 子代理（delegate_task + skill，由 agent-delegation-skills change 引入，未归档）当前是"黑盒"：执行期间前端只有开始/结束两个状态、无中间过程可见；子代理内部无 model turn/usage/超时日志，排障靠猜静默段；且子代理 thinking 未跟随请求级 deep_thinking（skill 声明 model 未声明 thinking 时落入模型默认思考，实测 qwen3.8-max 默认思考下中等题 154s，触发既有 DELEGATE_TIMEOUT=120s）；fork 超时用单一 120s 总时长无法区分"正常长思考"与"卡死不吐字"。

本次以"共享 LangGraph 事件管道 + scope(main|delegate)"为主线：子代理过程可见（双通道/过程面板）、子代理轮次日志、防污染路由、thinking 跟随、分层防失控超时，并落地长 fork 的 SSE 续流前提。**温度分档与 Task 看板拆为独立 change**（见 Changes 关系）。

## What Changes

- **共享事件转换管道 + scope**：`agent_service` 的事件转换从"按 `langgraph_node==agent` 判主答案"改为带 `scope(main|delegate)` 的共享转换器；fork 经显式接入同一管道。主/子观测代码复用一套，语义只差 scope 字段。
- **子代理过程前端可见（双通道）**：fork 的增量（thinking/正文）经独立 delegate SSE 事件推送（事件带 `delegate_id`/`skill`）；前端在回答内新增"领域专家分析过程"折叠区、按 delegate_id 分节；**不进**主回答气泡/full_answer。
- **子代理轮次日志**：新增 delegate 事件（start/end/model turn，含 model/elapsed_ms/usage/thinking/超时原因/结果长度），头部可区分主/子，随外层 trace 对齐。
- **防污染不变量**：delegate 事件禁止写入 full_answer/主 token 流（旧泄漏回归测试保留并改造）。
- **thinking 跟随**：fork 子代理在 skill 未声明 `thinking` 时默认 `enable_thinking = 请求级 deep_thinking`；skill 显式声明可覆盖。
- **fork 防失控分层**：事件级流空闲 watchdog（默认 60s 无任一事件即断）+ 总时长保险丝（默认 240s，deep_thinking=true 取 600s）+ turn 上限（零工具默认 5，开放工具后走 skill `max_iterations` 预留字段）；中断原因（idle/total/turn）入日志与前端文案，状态区分"完成/中断"。
- **SSE 主流续流与前端 EOF 恢复（前置，一并根治旧卡死）**：主 POST 流空闲收流与后台任务存活解耦（主 POST 订阅不再按 180s 空闲收流；resume 端点保留空闲错误兜底）；前端在干净 EOF 且未收到终态时按 `lastSeq` 自动续接（`onClose`→resume）。这是 600s 长 fork 可达性的时序前提。

## Capabilities

### New Capabilities
- `delegate-progress-observability`: 共享 scope 事件管道、子代理过程增量 SSE 与"分析过程"折叠区、子代理轮次日志、防污染路由
- `delegate-execution-controls`: fork thinking 跟随请求级 deep_thinking；分层防失控（idle/total/turn）与超时原因上报
- `sse-stream-resilience`: 主 POST 流空闲收流与后台任务解耦；前端干净 EOF 未收终态时自动 resume（onClose→lastSeq 续接）

### Modified Capabilities
- （无）——本 change 面向已实现但未归档的能力演进，统一以新 capability 承载，避免与 in-flight change 的 delta 冲突。

## 与其它 change 的关系
- **不含**温度分档（由独立 change `chat-temperature-policy` 承载）与 Task 任务看板（由独立 change `task-board` 承载，依赖本 change 的 delegate_id/task 事件语义）。
- **实施顺序（统一约束）**：`core → 温度(可并行，无共享文件) → task-board`。core 与 task-board 共享文件链（`sse.py`/`from_payload`/SSEEvent 联合、`api_contract.md`、`LOG_PREFIXES`/EventSpec、`chat.html`、`data-flow.md`）须**顺序合并**，并行实现会互踩——task-board 必须在 core 之上应用。
- **与 agent-delegation-skills 重叠处置**：该未归档 change 的 `delegate-observability` delta（SSE 委派状态 start/end、LLM 内容观测不产生 MODEL_TURN）由本 change 演进（start/end 带 delegate_id/ok-reason、区分完成/中断、新增轮次日志与过程增量）。**归档本 change 前，须对 agent-delegation-skills 的 `delegate-observability` delta 标注 SUPERSEDED**（core tasks 6.4），避免两批 ADDED 在归档同步时冲突/重复。

## Impact

- `src/agents/skills/executor.py` — fork astream 接入、thinking 继承修复、idle/total/turn 计时、超时原因记录
- `src/agents/skills/delegate_task.py` — 过程增量/start-end 串接（delegate_id/ok-reason）
- `src/services/agent_service.py` — `_convert_event`/`_drain` scope 重构、delegate 事件映射
- `src/utils/sse.py` — delegate 事件序列化 + `from_payload` + SSEEvent 联合（含 resume 回放路径）
- `src/infra/llm/request_context.py` — 增 `deep_thinking`（流入口 set）
- `src/config/const.py` / `settings.py` — fork idle/total 阈值、中断文案、时序不变量校验
- `src/agents/skills/models.py` / loader — 启用 `max_iterations`
- `src/chat/streaming.py` — 主 POST 流订阅与任务存活解耦（resume 端点保留空闲错误）
- `src/api/chat.py` / `src/api/sessions.py` — 主流订阅参数、续接语义
- 前端 `deploy/nginx/html/chat.html` — "分析过程"折叠区 handler + `onClose`→resume 恢复
- `src/core/log_events.py` / `log_event_specs.py` — `[delegate]` 前缀/事件注册 + `LOG_PREFIXES`（import 校验）+ 测试
- 测试与归属文档：`docs/agents/api_contract.md`/`data-flow.md`、glossary（按需）
