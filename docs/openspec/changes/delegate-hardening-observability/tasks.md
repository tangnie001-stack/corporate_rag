## 1. SSE 主流续流与前端 EOF 恢复（长 fork 前置）

- [ ] 1.1 `streaming.py`/订阅参数化：主 POST 流订阅 `max_idle=None`（空闲不收流，终态由任务生命周期提供）；resume 端点保留 180s 空闲错误兜底（单测：活跃任务长静默不断流 / 无活跃任务 resume 仍超时）
- [ ] 1.2 `chat.py`/`sessions.py`：主 POST 订阅传 `max_idle=None`、resume 传默认；确认与 `_run_with_finalize` 全路径终态（done/error）兼容；覆盖合法静默（ask_user 澄清等待、fork 无事件但任务存活）不断流、终态必达
- [ ] 1.3 前端 `chat.html`：`buildStreamHandlers` 增加 `onClose`——仅当 `state.current===STATE.STREAMING`（未收 done/error 终态）时按 `lastSeq` 调 `resumeStream`；已收终态 EOF 不续接
- [ ] 1.4 冒烟：长 fork/长静默场景确认主流不被 180s 收流；断线经 onError/onClose 自动恢复（含 600s 深思考档）
- [ ] 1.5 回归：旧"judge 静默→卡死"场景改为经续接恢复

## 2. 上下文与配置准备

- [ ] 2.1 `RequestContext` 增加 `deep_thinking` 字段（行内注释注明来源/范围/用途）
- [ ] 2.2 `chat_stream`/stream 入口按请求 set `ctx.deep_thinking`
- [ ] 2.3 `settings.py` 增加可调默认：fork idle 阈值、fork total（deep_thinking 档）；时序不变量注释（fork 事件级活动 < nginx/续流阈值）
- [ ] 2.4 `const.py` 登记超时/中断相关常量与文案：`DelegateStopReason` 枚举（normal/idle/total/turn/failed/cancelled）、"分析中断（原因）"文案
- [ ] 2.5 `SkillRecord` 启用 `max_iterations`（loader frontmatter 解析 + executor 消费，零工具默认 5）

## 3. fork 执行：astream + 防失控 + thinking 继承

- [ ] 3.1 `_resolve_fork_llm`：skill 未声明 thinking 时按 `ctx.deep_thinking` 设 `enable_thinking`；声明则覆盖（含单测）
- [ ] 3.2 `_run_fork` 改消费 `sub_agent.astream_events(..., version="v2")`：content/reasoning_content 聚合、最终文本提取与截断对齐原 ainvoke（保留 `var_child_runnable_config` 隔离）；改造既有 mock ainvoke 的 executor 测试
- [ ] 3.3 三层防失控 + 请求取消：事件级 idle（60s 无任一事件断，任一事件含 reasoning 即重置）/ total（240s，deep_thinking 600s）/ turn 上限（max_iterations）/ 响应 ctx.abort_signal（cancelled）；统一收敛为超时文案 + `DelegateStopReason`
- [ ] 3.4 delegate end 语义：delegate_task finally 按 ok/reason 分支——正常推"完成"，中断（idle/total/turn/cancelled/failed）推"分析中断（原因）"，不再无条件推完成
- [ ] 3.5 增量经 `ctx.clarify_channel` 投递 delegate 事件（kind=thinking/content、delegate_id/skill），投递前聚合/节流（约 50-100ms 或按条封顶）
- [ ] 3.6 executor 单测：astream 聚合、thinking 继承、idle/total/turn/cancel 各自触发、结果截断回归、节流后条数封顶
- [ ] 3.7 fork 请求取消路径单测：cancel 置位 ctx.abort_signal → fork 中断且原因=cancelled

## 4. 共享 scope 管道、delegate 事件与日志

- [ ] 4.1 `agent_service` 事件转换重构为带 `scope(main|delegate)` 的共享转换器（`_convert_event`/`_drain`），main 行为不变
- [ ] 4.2 `sse.py` 新增 delegate 事件序列化；同步 `from_payload` 与 SSEEvent 联合（未知类型 raise）；含 resume 回放测试
- [ ] 4.3 `log_events.py`：`LOG_PREFIXES` 增 `delegate` + Event 成员（`delegate start`/`delegate model turn`/`delegate end`）；`log_event_specs.py` 登记 EventSpec（import 期一致性校验）
- [ ] 4.4 delegate 事件携带 delegate_id/skill，end 带 ok/reason；主 token 流与 delegate 严格隔离（不写 full_answer）
- [ ] 4.5 既有"子代理泄漏"回归测试改造为 scope 防污染不变量（delegate→主答案/落库为空）
- [ ] 4.6 转换与日志单测：main/delegate 两 scope 映射、日志字段、usage 缺失走 estimate_usage 兜底（标 usage_estimated）

## 5. 前端 UI（过程面板）

- [ ] 5.0 前置：实现前先读设计稿 `docs/design/pages/chat-delegate-progress-2026-09-07.md`，采用 `/frontend-design` skill 产出；改动同步更新设计文档（防腐）

- [ ] 5.1 chat.html：`delegate` 事件 handler → 回答内"领域专家分析过程"折叠区（thinking 折叠/正文流式；按 delegate_id 分节；不触主气泡全文）
- [ ] 5.2 委派终态文案区分"完成/中断(原因)"
- [ ] 5.3 playwright 冒烟：单次与多次委派——过程分节可见、中断原因可辨、主答案与落库无子代理过程文本、EOF 自动恢复

## 6. 文档与收尾

- [ ] 6.1 logging-rules.md 前缀表登记 `[delegate]` 及事件（开放登记制）
- [ ] 6.2 归属文档同步 api_contract.md（`delegate` 契约增量）：
  - SSE 事件表（2.3.1）新增 `delegate` 事件行：`kind=thinking|content`、`delegate_id`、`skill`、`delta`
  - 更新「`delegate` 状态阶段」小节：end 携带 `ok`/`reason`，文案"领域专家分析完成" / "分析中断·原因"（不再无条件"完成"）；示例帧更新
  - `2.4.4 sessions/events` 续接语义补充：主 POST 流不再按 180s 空闲收流；resume 保留空闲错误；前端 onClose→lastSeq 续接（契约行为说明）
  - data-flow.md（链路/事件）；glossary 登记新术语：delegate_id、DelegateStopReason（含各原因）、delegate end ok/reason
- [ ] 6.3 openspec validate 通过；质量门禁（pytest/ruff/pyright）全绿
- [ ] 6.4 归档前检查：对 agent-delegation-skills 的 `delegate-observability` delta 标注 SUPERSEDED（防两批 ADDED 归档冲突）
