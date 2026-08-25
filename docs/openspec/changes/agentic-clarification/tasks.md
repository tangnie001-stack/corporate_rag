## 1. Agent 循环核心（阶段一）

- [ ] 1.1 新建 `src/agents/tools/rag_tools.py`：`retrieve_kb` 工具（包装 search + rerank_results + format_context，**全局递增编号** offset=len(tool_contexts)，InjectedState 读写，闭包注入 kb_ids）
- [ ] 1.2 `rag_tools.py` 增加 `ask_user` 工具：schema（questions[id/question/dimension/multi_select]），KB 聚合注入 options（`aggregate_kb_entities` + `SUGGESTIONS_MAP` fallback），挂起 Future + abort 信号 + ASK_USER_TIMEOUT；**async 工具 + contextvar 读 per-request 对象**（澄清通道/abort/注册表）
- [ ] 1.3 `state.py` 新增字段：`messages: Annotated[list[BaseMessage], AddMessages]`、`tool_contexts`、`_agent_iterations`、`_max_agent_iterations`、`_ask_count`、abort 信号引用、挂起澄清引用；删除 classify 相关字段与常量
- [ ] 1.4 新增 `src/agents/graph/agent_node.py`：`make_agent_node`（model↔tools 条件循环，`bind_tools` + ToolNode + tools_condition，迭代上限 + 超时 + 循环检测，护栏计数经 InjectedState；**循环结束提取末次 AIMessage → state.answer**；初始注入 system + 历史 + query 组装 messages）
- [ ] 1.5 `workflow.py`：图改为 `kb_router → agent 循环 → format`；删除 classify/rewrite/retrieve/rerank/generate 节点与全部相关边
- [ ] 1.6 `nodes.py`：删除 `make_classify_node`/`make_rewrite_node`/`make_retrieve_node`/`make_rerank_node`/`make_generate_node` 工厂函数

## 2. 交互基建（后端）

- [ ] 2.1 新增挂起澄清注册表（session_id → Future，单会话并发拒绝，abort/超时清理）
- [ ] 2.2 `agent_service.py` 双路事件合并：Task A 跑 astream_events 推 queue + Task B drain queue 作 SSE 生成器；**queue 哨兵收尾**（ErrorMarker/EndMarker）+ Task B finally set abort + cancel task_a + gather
- [ ] 2.3 新增 `POST /api/chat/clarify-answer`：校验 session 归属 → resolve 挂起 Future → 答案作为 user 消息**到达即写** Redis
- [ ] 2.4 `sse.py` 新增 `SSEAskUserEvent`（questions/options/multi_select）、`SSEAbstentionEvent`（abstention 标识 + 转人工标记），`SSEClarificationEvent` 退役
- [ ] 2.5 新增 per-session 并发锁（Redis SETNX 带 TTL，请求开始获取 finally 释放，冲突返回 409）
- [ ] 2.6 历史窗口注入：最近 N 轮 + token 双上限截断（**初始注入前对 `_history` 截断**），最近 1 轮完整注入
- [ ] 2.7 `agent_service.py` 状态事件改按事件类型发（on_chat_model_start/on_tool_start/on_tool_end），删除 `SSE_STATUS` 节点名映射；model_used 改从 on_chat_model_end 捕获

## 3. abort 语义

- [ ] 3.1 `api/chat.py`：创建 per-request abort 信号，断连检测（StreamingResponse 取消 / request.is_disconnected）置位
- [ ] 3.2 agent 节点 LLM 改用 `llm.astream()`（异步），循环边界检查信号
- [ ] 3.3 ask_user Future 绑定 abort 信号（断连即 resolve + 清理注册表）
- [ ] 3.4 持久化策略（user 到达即写、assistant 完成/中止时写）：部分答案标记 interrupted 落库；SSE 静默收尾不写 done

## 4. 前端 composer 化

- [ ] 4.1 `chat.html` 新增 `ask_user` 事件监听器（原 clarification 监听器删除，移除 `source.close()`），隐藏 textarea，渲染表单（radio/checkbox + custom + 多 section）
- [ ] 4.2 `submitClarification` 改为 `fetch POST /api/chat/clarify-answer`，不调 `startSSE`；非 2xx（如超时 404）关闭 composer + 提示重问
- [ ] 4.3 断连处理：CLARIFYING 期间显式 `source.close()` 放弃重连，移除表单恢复输入
- [ ] 4.4 反馈按钮：每条回答下 👍/👎 + 可选原因，`POST /api/feedback`
- [ ] 4.5 超时前端处理：CLARIFYING 状态收到 done → 关闭 composer、恢复输入、提示"该问题已超时，请重新提问"
- [ ] 4.6 abstention 转人工入口：收到 `SSEAbstentionEvent` → abstention 样式 + "转人工咨询"入口（点击记录会话标记）

## 5. 反馈与可观测性

- [ ] 5.1 新增 `POST /api/feedback` 接口 + 反馈落库（session_id/message_index/rating/comment/trace_id）
- [ ] 5.2 全流程节点日志可查：每个图节点进出日志（节点名/入参摘要/输出摘要/耗时），含 agent 循环节点，经 trace_id 可还原执行链
- [ ] 5.3 工具调用日志：agent 循环每步键值对（iteration/tool/args_summary/result_summary/latency/tokens）
- [ ] 5.4 护栏告警：迭代上限/ask_user 超时/超限/abort → warn 级（含 query 与轨迹）
- [ ] 5.5 循环 token 用量聚合，turn 结束输出总量与调用次数

## 6. 阶段二：转人工

- [ ] 6.1 阶段一 abstention 前端"转人工咨询"入口（纯 UI + 会话标记）
- [ ] 6.2 `rag_tools.py` 增加 `escalate_to_human` 工具（fire-and-forget，工单落库 + 返回单号，per-turn 上限 1 次）
- [ ] 6.3 工单产品层（状态流转/通知/人工控制台）——独立于 agent 循环

## 7. 测试与契约

- [ ] 7.1 工具单测：retrieve_kb 全局递增编号一致、ask_user options 注入与超时、escalate 上限
- [ ] 7.2 双路合并注入式单测：fake 事件源 + 真 queue，验证哨兵（EndMarker/ErrorMarker）、取消传播
- [ ] 7.3 SSE 集成测试（httpx）：真实 /chat/stream + /clarify-answer，验证澄清后同流续答（SSEAskUserEvent → POST 答案 → 继续收 token）
- [ ] 7.4 abort 集成测试：客户端断开 → graph 任务取消、ask_user Future 唤醒、挂起注册表清理、锁释放
- [ ] 7.5 更新 `tests/agents/graph/test_graph.py`（删 classify/固定流水线断言，加 agent 循环接线断言）
- [ ] 7.6 更新 `tests/services/test_agent_service.py`（SSE 事件序列、并发锁 409、历史窗口截断）
- [ ] 7.7 更新 `docs/agents/api_contract.md`（clarify-answer/feedback/SSEAskUserEvent 契约，SSEClarificationEvent 退役）
- [ ] 7.8 归档 `query-rewrite-and-graph-simplification`：rerank 去阈值与 RRF 多查询融合逻辑吸收进 retrieve_kb 工具任务；rewrite/classify/grader/batch-clarification 任务作废
