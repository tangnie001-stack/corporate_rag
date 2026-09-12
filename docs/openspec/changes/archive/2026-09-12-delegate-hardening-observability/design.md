# delegate-hardening-observability Design

## Context

fork 子代理（delegate_task + skill，agent-delegation-skills change 引入，未归档）当前可观测性极弱：
- 前端仅收到"正在调用领域专家分析…/领域专家分析完成"两个状态点（既有 delegate-observability delta 的 SSE 委派状态），过程为黑盒；
- 子代理 LLM 调用只经 LlmContentLoggingHandler（LLM_LOG_CONTENT 开时）记录，无 model turn/usage/超时结构化日志；
- 曾因子代理事件经 `var_child_runnable_config` 自动泄漏到主图、污染 full_answer（Critical），修复方式为 `var_child_runnable_config.set(None)` 一刀切隔离——隔离正确，但也关闭了子代理过程展示的可能；
- 子代理 thinking 与请求级 deep_thinking 不一致（skill 声明 model 未声明 thinking → 新建实例不带 enable_thinking → 落入模型默认思考），实测 qwen3.8-max 默认思考下中等题 154s，触发 DELEGATE_TIMEOUT=120s；
- 主 POST 流 SSE 订阅空闲 180s 收流 + 前端干净 EOF 为 no-op，曾是"judge 静默→输入框卡死"根因；长 fork（600s）必须先把这条时序解开。

约束：单 worker、进程内 streaming 状态；日志规范 logging-rules.md（前缀开放登记制）；本 change 不含温度分档与 Task 看板（各自独立 change）。

## Goals / Non-Goals

**Goals:**
- 子代理执行过程对用户可见（思考/正文增量 + 阶段），不进入主答案/落库内容
- 子代理有结构化轮次日志（model/usage/elapsed/thinking/超时原因），与主链 trace 对齐
- fork thinking 与请求级 deep_thinking 一致（skill 可显式覆盖）
- fork 防失控：事件级流空闲 + 总时长保险丝 + turn 上限，中断原因可辨
- 长 fork 的 SSE 续流前提（主 POST 流不因空闲收流；前端 EOF 自动续接）

**Non-Goals:**
- 不做主 agent 温度分档（独立 change `chat-temperature-policy`）
- 不做 Task 任务看板（独立 change `task-board`）
- 不引入独立异步任务框架；不引入 skill temperature 字段与"探讨型 skill 高温度"
- 不做 judge/离线评估

## Decisions

### D1 共享事件转换管道 + scope
主/子事件转换收敛为共享转换器，入参 `scope ∈ {main, delegate}`。main 侧保持现有输出（token/status→答案）；delegate 侧输出 delegate 事件（增量/工具步）与 delegate 日志。scope 由显式接入点确定，不靠事后按 `langgraph_node` 猜归属。
**备选**：主/子两套转换 → 漂移与二次污染风险，否决。

### D2 fork 用 astream 级消费
`_run_fork` 改为消费 `sub_agent.astream_events(input, config=..., version="v2")`（零工具下实际单次模型调用）：
- 逐事件聚合 `content`/`reasoning_content`（ChatQwenWithReasoning 已把 reasoning 放入 chunk additional_kwargs）；
- 增量转发至 `ctx.clarify_channel`（delegate 事件，带 delegate_id/skill）并重置 idle 计时；
- `on_chat_model_end` 收 usage（缺失走 estimate_usage 兜底并标 usage_estimated）；链结束取最后 assistant 文本为结果，仍受 `DELEGATE_RESULT_LIMIT` 截断；
- 保留 `var_child_runnable_config` 隔离；显式 config 透传 tags=["delegate"]。
**备选**：ainvoke+回调 → 拿不到增量、无法 idle，否决。

### D3 防失控三层（executor）
- **事件级 idle**：收到子代理任一事件（reasoning/content/工具事件）即重置；超 `DELEGATE_MAX_IDLE_S`（默认 60）无事件 → 中断（原因=idle）。qwen 思考为流式增量，正常长思考持续吐字不误杀；完全静默才断。
- **total 保险丝**：`asyncio.wait_for(..., timeout=...)` 默认 240；`ctx.deep_thinking` 为 true 取 600。
- **turn 上限**：启用 `SkillRecord.max_iterations`（零工具默认 5，防御；开放工具后生效），按 agent 步计数。
- **请求取消（abort_signal）**：fork 执行响应 `ctx.abort_signal`（cancel 端点置位），中断原因=cancelled；与 idle/total/turn 共用中断收敛路径与 `DelegateStopReason`。
- **统一原因枚举**：`DelegateStopReason = normal|idle|total|turn|failed|cancelled` 集中于 const，delegate end 与 task 终态共用。
- 中断统一回"超时文案"给主 agent；**状态区分**：正常→"领域专家分析完成"，idle/total/turn/cancelled/failed→"分析中断（原因）"（不再无条件推"完成"，delegate_task finally 需按 ok/reason 分支）。
依据：claude-code 用 maxTurns；deepseek-harness 用流空闲 + abort。

### D4 thinking 跟随：resolve_fork_llm 读 ctx.deep_thinking
- skill 声明 thinking → 用声明值（显式覆盖）；
- 未声明 thinking → `extra_body.enable_thinking = ctx.deep_thinking`（新建实例携带，与
  spec「fork thinking 跟随请求级 deep_thinking」一致）；`ctx` 不存在时退回复用主 agent 实例；
- 需新建实例携带 enable_thinking，但 skill 未声明 model 且主 agent 无 model_name 可继承
  （测试替身/缺省）→ 复用作兜底并记 warning（避免落到默认 LLM_MODEL 造成模型漂移）。
`RequestContext` 新增 `deep_thinking`（`agent_service.stream_chat` 建 ctx 处按请求 set，是
`chat_stream` 请求深思考开关的 fork 消费通道）。

### D5 SSE 续流与前端 EOF 恢复（长 fork 前置）
- **主 POST 流**：以参数区分主/续接订阅——主 POST 订阅 `max_idle=None`（空闲不收流），终态由任务生命周期提供（`_run_with_finalize` 全路径兜底：正常/异常/取消均补 done/error）；`/api/sessions/events`（resume）保留 180s 空闲错误，防"无任务僵尸续接"。不依赖 `is_running` 运行时判定（避免判空竞态）。
- **前端 onClose**：干净 EOF 且 `state.current === STATE.STREAMING`（即未收到 done/error 终态）时，按 `lastSeq` 调 `resumeStream` 续接；收到终态后 EOF 不续接。
- **nginx 300s**：fork/任务事件本身提供活跃流量；即使被 300s 掐断，前端 `onError` 既有自动重试/`onClose` 恢复会自行 resume——**心跳为可选优化，非必需**（此前 delegate 超长轮即靠该路径存活）。
- **合法静默不误断**：ask_user 澄清等待、fork 无事件但任务存活等合法静默均不因 180s 空闲策略断流；终态由服务端超时/done 收口（tasks 1.2 覆盖该交叉场景）。
同时根治旧"judge 静默 → 收流 → 卡死"问题。

### D6 delegate/task 事件标识
delegate 增量、start/end 均携带 `delegate_id`（及 skill）；end 带 `ok`/`reason`（normal/idle/total/turn/failed）。前端"分析过程"折叠区按 delegate_id 分节，一次回答多次 delegate 不串流。（task 事件协议由 `task-board` change 消费本语义。）

### D7 过程区持久化与缓冲边界
- 过程增量经聚合后（约 50-100ms/按条封顶）进入事件缓冲，用于运行期与 resume 补差；
- **不保证** resume 回放完整过程原文（缓冲 2000 条有界，600s 思考可能淘汰尾部）——保证的是主 token/终态与 delegate 终态不丢；
- 过程区/看板原文为**运行期展示**：刷新或历史重载后不恢复过程原文，仅保留最终答案（写入消息存储的仍只是主 agent 整合文本）。

### D8 日志与事件注册链
- 新前缀 `[delegate]`（登记 logging-rules.md）与事件 `delegate start`/`delegate model turn`/`delegate end`；
- 同步链：`log_events.LOG_PREFIXES` + Event 成员 + `log_event_specs.EVENT_SPECS`（import 期一致性校验）+ `sse.py` 序列化/`from_payload`/SSEEvent 联合（未知类型 raise）→ 缺任一步即 import/resume 崩，按链一次改完并补 resume 回放测试。

## Risks / Trade-offs

- **scope 判定错误 → 污染复发** → 保留改造泄漏回归为防污染不变量；scope 由显式接入点定。
- **astream 改造回归** → 保留 executor 单测并新增 astream/超时路径测试；结果聚合与截断对齐原实现。
- **增量高频/大 payload** → 转发聚合节流；日志只按轮次记，不逐 token 落日志。
- **缓冲有界导致过程原文不全回放** → 明示（D7）只保证主 token/终态与 delegate 终态。
- **主 POST `max_idle=None` 理论上无限挂起** → 终态由任务生命周期兜底；任务若进程级卡死属另一类事故，由外层健康机制处理（既有范围）。
- **事件类型链式漏改即崩** → D8 一条链 + resume 测试。
- **流式 usage 缺失** → estimate_usage 兜底 + usage_estimated 标注。

## Migration Plan

- 可独立回退提交顺序：① SSE 续流（streaming/api/前端 onClose）→ ② executor（astream + 三层防失控 + thinking 继承）→ ③ scope 管道/delegate 事件/日志（sse/from_payload/LOG_PREFIXES/EventSpec 一条链）→ ④ 前端过程折叠区。
- 无数据迁移；rollback=回退对应提交。
- 冒烟：一次委托场景确认过程分节可见、超时原因可辨、"完成/中断"文案正确、主答案与落库无子代理过程文本；断开/长跑场景确认 onClose/onError 自动续接。

## Open Questions

- 无阻塞项。过程原文保留策略已定（D7 运行期展示）；Task 工具暴露范围交由 `task-board` change 自行定义。
