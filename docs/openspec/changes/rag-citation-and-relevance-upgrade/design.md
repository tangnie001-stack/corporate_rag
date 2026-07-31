# Design: RAG 引用落地校验与低相关度门控

## Context

trace `trace_50eaa1bb-00d9-4422-a8e2-3c1c137fee33`（query="阿里巴巴"）暴露三问题：

1. **回答丢失**：`agent_service.py:121` 用事件 `name`（模型类名 `ChatOpenAI`）判断是否属于 generate 节点，恒不匹配，回答 token 全被过滤。已实证：LangGraph `on_chat_model_stream` 事件的 `metadata.langgraph_node` 才是节点名。
2. **引用失真**：`nodes.py:221-240` 的 `format_node` 把所有 contexts 去重列出，无落地校验。
3. **低相关无门控**：`retrieval.py:117` rerank 后无分数阈值；grader 重试耗尽降级后 `generate_node` 仍带全部低分 context 生成。

当前数据流（相关部分）：

```
retrieve → grader(关键词覆盖度, <0.5 重试×2→downgrade)
        → rerank(8→5, 无阈值)
        → generate(带5条context, prompt含"[1] 来源:...")
        → format(全部去重列出→5条citations)
        → agent_service 输出 citations 全部发送
```

关键现状事实（已查证）：
- `PromptManager.get_system_prompt()` 已内置内联引用指令 `_INLINE_CITATION_INSTRUCTION`（"引用文档时请在句末标注编号 [1][2]"），无论 Langfuse 或本地兜底都会追加——**LLM 侧引用标记约束已在线**。
- `agent_service.py` 的 Generate `CHAIN_END` 只取 `model_used`/`is_fallback`，**不取 `answer`**；引用事件由 agent_service 从 `contexts` 快照自行生成，**不消费 format_node 输出**。
- `build_simple_prompt` 仅 `generate_node` 一处调用（empty-contexts 分支）。

## Goals / Non-Goals

**Goals:**
- 修复 SSE 回答 token 丢失 bug
- 引用只展示回答实际引用的来源；拒答时不展示引用
- rerank 分数阈值门控；无达标 context 走 abstention 出口
- 问候/闲聊（simple）保持免检索直接回答，不受 abstention 影响
- 保持现有节点结构，最小改动

**Non-Goals:**
- 不做 LLM-based grader 升级（关键词覆盖度保留，另开变更）
- 不做 RAGFlow 式后端相似度重注入引用（方案 A 已足够）
- 不改前端 UI 结构（仅容忍"无引用"和 `model=""`）
- workflow 层 simple→direct 免检索路由（后续优化，本次不做）

## Decisions

### D1: 事件过滤改用 `metadata.langgraph_node`

`agent_service.py` 的 `CHAT_MODEL_STREAM` 分支，从 `name` 匹配改为 `metadata` 匹配：

```python
metadata = event.get("metadata", {}) or {}
node_name = metadata.get("langgraph_node", "")
if LangGraphNode.Generate.NAME not in node_name:
    ...filtered log...
    continue
```

- **为什么**：LangGraph v1.2.9 `on_chat_model_stream` 事件 `name` 为模型类名，节点名在 `metadata["langgraph_node"]`。已用最小图实证。
- **备选**：从 generate 的 `CHAIN_END` output 里取 `answer`（一次性返回，失去流式效果）→ 否，保留流式。

### D2: 引用落地过滤（prompt 已内置指令，仅补解析与过滤）

**D2a（已确认，prompt 不动）**：LLM 侧引用指令已由 `PromptManager._INLINE_CITATION_INSTRUCTION` 保证，**不修改 `USER_PROMPT_TEMPLATE`**。仅核对 `format_context` 编号 `[1] 来源:` 与指令一致（已一致）。

**D2b: `format_node` 落地过滤**：从 `state.answer` 正则提取 `\[(\d+)\]` → 映射到 contexts → 只保留被引用的 source（按 source/page 去重）。非法编号（超出范围）忽略。拒答检测：回答含"未在文档中找到"等拒答语 → citations 置空。

**D2c: `agent_service` 消费 format_node 输出**（关键，否则 D2b 白改）：

```python
elif LangGraphNode.Format.NAME in name:
    formatted_citations = output.get("citations", [])
# ...
citations_to_send = formatted_citations   # 优先 format_node 过滤结果
if not citations_to_send:                 # 兜底：未捕获时沿用旧逻辑
    for ctx in contexts: ...
for c in citations_to_send:
    yield SSECitationEvent(...)
```

### D3: rerank 分数阈值 `RERANK_MIN_SCORE`

`src/config/settings.py` 新增 `RERANK_MIN_SCORE`（默认 0.3）。`rerank_results` 过滤 `score < RERANK_MIN_SCORE` 的 context。

**关键边界（已确认）**：rerank **失败**的 fallback 分支（`retrieval.py:102-114`，分数为 `1-distance`，量纲不同）**不应用阈值**，保持透传——异常降级路径宁滥勿缺，靠 D2 引用过滤兜底。

- **为什么 0.3**：cross-encoder 输出量纲，可配置起点。trace 中 0.18 应被过滤，正常高相关（top_score 0.9）不受影响。

### D4: abstention 分支

`generate_node` 中 empty-contexts 分支不再走 Naive RAG，改为静态拒答文案：

```python
if state.skip_retrieval:          # ① 问候/闲聊：免检索直接回答（build_simple_prompt 保留）
    prompt = build_simple_prompt(query, state._history or [], prompt_manager)
elif not contexts:                # ② 检索无结果：abstention
    answer = ABSTENTION_TEXT      # 静态文案，不回 LLM
else:                             # ③ 正常 RAG 生成
    ...
```

- **skip_retrieval 判断必须在最前**（拷问 3）：问候查询即使有达标 context 也走 build_simple_prompt，避免被 RAG 污染。
- abstention 文案放 `src/config/prompts.py` 常量，不硬编码。
- `build_simple_prompt` **保留**，服务 ① 问候路径（拷问 8）。
- 多轮对话"检索无结果但历史有上下文"的误伤风险：本次不做，记 Open Question。

### D5: SSE 事件链修正（abstention 完整送达前端）

**D5a: 捕获 Generate 的 answer 并兜底发送**（拷问 1，否则 abstention 文本消失）：

```python
elif LangGraphNode.Generate.NAME in name:
    answer = output.get("answer", "")
    model_used = output.get("model_used", model_used)
    is_fallback = output.get("is_fallback", is_fallback)
# ...
# 流结束后、发引用前：
if not full_answer and answer:     # abstention / 静态回答路径
    full_answer = answer
    yield SSETokenEvent(answer)    # 先文本后引用，与正常链路一致
# 持久化
if full_answer:
    await self._chat_manager.add_message_async(...)
```

**D5b: abstention 事件顺序与状态**（拷问 6）：
- 补充 token 事件放在 citations 循环**之前**（先文本后引用）
- abstention 时额外发 `SSEStatusEvent` 提示（如"未找到相关文档，已直接答复"）
- `generate_node` abstention 分支**显式设置** `model_used=""`、`is_fallback=False`，避免状态污染
- `SSEModelInfoEvent` 保留，`model=""` 时前端需容忍

### D6: simple 路径的 skip_retrieval 标记

- `QueryRouter._simple_result()` 增加 `"skip_retrieval": True`（问候/≤2字符专属）
- `classify_node` 透出 → `AgentState` 加 `skip_retrieval: bool = False`
- workflow 层 simple→direct 免检索路由**本次不做**（问候仍白检索一次，行为正确、改动最小），记后续优化

### D7: 引用条目编号徽标（前端联动）

`SSECitationEvent` 增加 `index` 字段（= format_context 中的原文档编号 n，与回答里的 `[n]` 标记一致），前端引用条目显示 `[n]` 徽标，使回答中的引用标记与列表条目可对应。

```python
# sse.py — SSECitationEvent 增加字段
index: int = 0  # 原文档编号（对应 format_context 的 [n]）
```

- **为什么**：D2 过滤后引用列表只留被引用的（如 `[1]`、`[3]`），前端若按事件顺序展示而无法对号入座，`[3]` 对哪一条全靠猜。带编号后语义闭合。
- **改动范围**：`sse.py` 加字段 + `format_node`/`agent_service` 传 index + 前端条目加 `<span>` 徽标（约 15 行）。
- **不做点击跳转**（回答文本 `[n]` → 可点击角标 + 高亮联动）：超出本次后端修复范围，记后续优化。
- **备选**：B（纯来源列表，前端零改动）→ 回答中裸 `[n]` 与列表无法对应，歧义仍在，不采用。

## Risks / Trade-offs

- [引用标记漏标/错标] → 正则提取只保留合法编号 + 拒答置空兜底；格式错误不阻断回答流
- [阈值误杀真相关] → `RERANK_MIN_SCORE` 可配置，上线后按测试集校准
- [rerank 失败绕过阈值] → 已确认 fallback 不应用阈值，靠 D2 兜底
- [abstention 多轮对话误伤] → Open Question，后续可升级为"LLM 结合历史回答 + 无引用"
- [前端依赖引用/模型名] → 前端需容忍空引用和 `model=""`（实现时同步检查）
- [session 重复插入 bug（Duplicate entry）] → 已有问题，与本次无关，记观察项不修

## Migration Plan

1. 修复 bug（D1）→ 验证回答 token 正常流出
2. D2c + D2b（引用过滤链路）→ 引用只留被引用项
3. D3 + fallback 边界（阈值门控）
4. D4 + D5（abstention 出口与事件链）
5. D6（skip_retrieval 标记）
6. 全量 `pytest tests/ -v` + `ruff check .` + 手动复测 trace 场景

回滚：各步骤独立提交，可逐个 revert。

## Open Questions

1. ~~simple 问候是否免检索~~ → **已决议**：generate_node 最前判断 skip_retrieval 走 build_simple_prompt；workflow 层 direct 路由留后续优化。
2. abstention 文案是否需要区分"KB 无相关文档"与"query 表述不清"？——待产品确认，本次用统一文案。
3. 多轮对话"检索无结果但历史有上下文"是否应降级为 LLM 结合历史回答（而非拒答）？——本次 abstention，后续评估。
4. 前端是否需要在"无引用"时隐藏来源栏、`model=""` 时隐藏模型信息？（实现时检查 frontend/）
