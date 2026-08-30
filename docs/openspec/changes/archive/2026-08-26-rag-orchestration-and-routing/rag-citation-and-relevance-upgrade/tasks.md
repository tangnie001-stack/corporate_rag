# Tasks: RAG 引用落地校验与低相关度门控

## 1. 修复 SSE 回答 token 丢失 Bug（问题 1 / D1）

- [ ] 1.1 `src/services/agent_service.py` 的 `CHAT_MODEL_STREAM` 分支改为读取 `event["metadata"]["langgraph_node"]` 判断是否属于 generate 节点
- [ ] 1.2 验证 trace `trace_50eaa1bb-00d9-4422-a8e2-3c1c137fee33` 场景回答能流到前端（回答不再只出现在 filtered 日志）
- [ ] 1.3 在 `tests/services/test_agent_service.py` 补充 `CHAT_MODEL_STREAM` token 事件流到前端的测试（模拟 metadata.langgraph_node=generate）

## 2. 引用落地过滤（问题 2 / D2）

- [ ] 2.1 核对 `src/rag/prompt.py:format_context` 编号 `[1] 来源:` 与 `PromptManager` 内联引用指令一致（已一致，只做确认，不改 prompt）
- [ ] 2.2 `src/agents/graph/nodes.py:format_node` 从 `state.answer` 正则提取 `\[(\d+)\]`，映射到 contexts，只保留被引用的 source（按 source/page 去重）；保留每条的原始编号 index
- [ ] 2.3 非法编号（超出 context 范围）忽略
- [ ] 2.4 拒答检测：回答含"未在文档中找到"等拒答语时 citations 置空（拒答关键词放入 `src/config/prompts.py` 常量）
- [ ] 2.5 `src/services/agent_service.py` 增加 Format `CHAIN_END` 捕获 `output["citations"]`，优先用它发 `SSECitationEvent`（含 index）；未捕获时兜底回退遍历 contexts 的旧逻辑（兜底分支 index=0）
- [ ] 2.6 补充 `format_node` 单元测试：部分引用、非法编号、拒答三种场景

## 3. 引用条目编号徽标（D7）

- [ ] 3.1 `src/utils/sse.py` 的 `SSECitationEvent` 增加 `index: int = 0` 字段（原文档编号，对应 format_context 的 [n]），`to_sse`/`sse_citation` 同步
- [ ] 3.2 `format_node` 输出的 citations dict 增加 `"index"` 键（原始编号）
- [ ] 3.3 `agent_service.py` 发送 `SSECitationEvent` 时传入 index
- [ ] 3.4 `frontend/chat.html` 引用条目增加 `[n]` 徽标（`renderCitation` 接收 index 并展示）
- [ ] 3.5 更新 `agent_service`/`sse` 相关测试覆盖 index 字段

> 硬编码归位约定（claude.md 规则）：本次新增常量按 `settings.py`（运行参数）/ `prompts.py`（文案）/ `const.py`（固定阈值、状态映射）归位，不散落业务代码；存量硬编码清理（grader 0.5、kb_router 0.82、query_router 2 等）另开变更，不混入本次。

## 4. Rerank 分数阈值（问题 3 / D3）

- [ ] 4.1 `src/config/settings.py` 新增 `RERANK_MIN_SCORE`（默认 0.3，可环境变量覆盖）
- [ ] 4.2 `src/rag/retrieval.py:rerank_results` 过滤 `score < RERANK_MIN_SCORE` 的 context
- [ ] 4.3 rerank 失败 fallback 分支（`1-distance` 分数）**不应用阈值**，保持透传
- [ ] 4.4 补充 `rerank_results` 测试：低于阈值的 context 被过滤、全部低于阈值返回空列表、rerank 失败 fallback 不过滤

## 5. Abstention 出口（问题 3 / D4）

- [ ] 5.1 `src/agents/graph/nodes.py:generate_node` 三分支：① skip_retrieval → `build_simple_prompt`（问候/闲聊，保留）；② 无 contexts → abstention 静态文案；③ 正常 RAG 生成
- [ ] 5.2 skip_retrieval 判断放在 generate_node **最前**，优先于 contexts 判断
- [ ] 5.3 abstention 文案放入 `src/config/prompts.py` 常量（不回 LLM）
- [ ] 5.4 abstention 分支显式设置 `model_used=""`、`is_fallback=False`
- [ ] 5.5 补充 `generate_node` 单元测试：skip_retrieval 有/无 context、检索无结果三种场景

## 6. SSE 事件链修正（D5）

- [ ] 6.1 `src/services/agent_service.py` Generate `CHAIN_END` 捕获 `output["answer"]`
- [ ] 6.2 流结束后、发引用前：`if not full_answer and answer:` → `full_answer = answer` 并 `yield SSETokenEvent(answer)`（保证 abstention 文本送达且先文本后引用）
- [ ] 6.3 abstention 时额外发 `SSEStatusEvent` 提示（提示文案放入 `src/config/const.py` 的 `SSE_STATUS` 映射）
- [ ] 6.4 持久化兜底：`if full_answer:` 下 `add_message_async` 覆盖 abstention 路径
- [ ] 6.5 补充 `agent_service` 测试：abstention 文本作为 token 送达、citations 为空、事件顺序（token 在 citation 前）

## 7. Simple 路径 skip_retrieval 标记（D6）

- [ ] 7.1 `src/infra/search/query_router.py:_simple_result` 增加 `"skip_retrieval": True`
- [ ] 7.2 `src/agents/graph/state.py:AgentState` 增加 `skip_retrieval: bool = False` 字段（带行内注释）
- [ ] 7.3 `classify_node` 透出 `skip_retrieval` 到 state
- [ ] 7.4 补充 `QueryRouter` 测试：问候/短查询 skip_retrieval=True，普通查询 False

## 8. 前端兼容与联动验证

- [ ] 8.1 检查前端引用栏在无引用事件时是否正常（不报错、可隐藏）
- [ ] 8.2 检查前端对 `model=""` 的容忍度
- [ ] 8.3 验证历史会话/查询详情页引用展示跟随过滤结果（手动）
- [ ] 8.4 观察项备注：`_persist_conversation` 的 session Duplicate entry 为已有问题，本次不修（trace 中已出现）

## 9. 验证闭环

- [ ] 9.1 `pytest tests/ -v` 全部通过
- [ ] 9.2 `ruff check .` 无错误
- [ ] 9.3 手动复测 trace 场景：query="阿里巴巴" → 回答正常流出 + 无引用（拒答）+ abstention 状态提示
- [ ] 9.4 手动复测正常场景：高相关 query → 回答含 `[n]` 标记 + 引用只列被引用来源 + 条目带 `[n]` 徽标
- [ ] 9.5 手动复测问候场景：query="你好" → 免检索直接回答，不受 abstention 影响
- [ ] 9.6 commit 变更，输出 `git diff HEAD~1` 供 review
