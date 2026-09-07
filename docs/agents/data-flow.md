# 数据流链路

## 链路 1：文档上传 → 解析 → 入库 ★

```
用户上传 → MinIO 存储 → 文档解析(parse) → 策略检测
→ 智能分块(chunk) → 分块质量校验 → [可选]分块质量评估
→ ChromaDB 向量入库 → MySQL 元数据更新(ready)
```

入口: `POST /api/kbs/documents/upload` → 后台 `asyncio.create_task(_process_document_task)`
文件类型: `.pdf` / `.docx` / `.txt`，异步返回 `202` + `doc_id`

## 链路 2：用户问答 — 绑 KB 检索问答 与 未绑 KB 纯对话 ★

```
用户提问 → POST /api/chat/stream → SSE 建立 → agent 循环 → verify → format
→ 引用(citations) → 对话历史持久化(Redis + MySQL)
```

入口: `POST /api/chat/stream`（body: `ChatStreamRequest`：session_id / kb_id / query / deep_thinking）
输出: SSE 事件流 `status → token → citation → model_info → done`（澄清/联网询问时含
`ask_user`，纯拒答时含 `abstention`，deep_thinking 时含 `reasoning`）

用户问答在入口按 `kb_id` 是否为空分裂为两条实质不同的链路：**链路 2a（绑 KB 问答链，
RAG 检索 + 验证）** 与 **链路 2b（未绑 KB 纯对话链，联网兜底）**。`kb_id` 由前端绑定，
`chat_stream` 透传给 `agent_service.stream_chat`，后者写 `ctx.kb_bound = bool(kb_id)`
（src/api/chat.py:401、src/services/agent_service.py:592）。分裂并非两条独立图，而是同一
StateGraph 拓扑（`agent ↔ tools 循环 → agent_finalize → verify → format → END`，
workflow.py:37-87）上的四处分叉：

1. **prompt**：未绑 KB 时在系统指令后追加 `KB_UNBOUND_SYSTEM_PROMPT`，禁止调用检索
   （src/agents/graph/agent_node.py:68-74 以 `kb_bound=bool(state.kb_id)` 调
   `build_prompt`；src/rag/prompt.py:42-43 注入）
2. **retrieve_kb 内部**：`kb_id` 为空直接返回空结果，不检索（rag_tools.py:127-130 硬保证）
3. **verify 节点**：按 `state.kb_id` 分派态 A / 态 B 两套校验链（verify/node.py:35-84）
4. **SSE 状态事件**：检索阶段与联网阶段文案不同（agent_service.py `_convert_event`）

工具注册表两条链路相同：retrieve_kb + ask_user 常驻，`WEB_SEARCH_ENABLED` 开时才追加
search_web（rag_tools.py:226-235）。全局开关影响两条链路：`VERIFY_ENABLED=false` →
verify 直通（settings.py:68-72）；`WEB_SEARCH_ENABLED=false` → 无联网兜底工具
（settings.py:99-104）；`TEMPORAL_PARSE_ENABLED=false` → 不解析年份，2a 完整性校验无
数据源、直接跳过（settings.py:62-66）。

```
agent ──(末条含 tool_calls 且未超限)→ tools ─→ agent（循环）
  │
  └(无 tool_calls / 达迭代上限)→ agent_finalize → verify ─(通过)→ format → END
                                                    └(_needs_regenerate)→ agent
```

### delegate 分支（主从委派，两条链路通用）

agent 在循环内判定任务需要领域专家能力（深度分析/专用方法论）时，可调用
`delegate_task(task, skill)` 委派给对应 skill——skill 库（顶层 `skills/`，volume 挂载）
有内容才注册该工具（src/services/agent_service.py:571-593），注册表懒重载
（reload_if_changed）。按命中 skill 的 `context` 分发两种执行形态：

```
agent 判定需领域专家 → delegate_task(task, skill)
  ├─ inline：skill 方法论注入 agent 上下文 → agent 自己继续走 2a/2b
  └─ fork：零工具子代理独立分析（材料由 agent 预检索后随 task 一并传入）
       → 纯文本结果回 agent → agent 整合进最终答案 → verify → format
```

- inline 执行无独立子代理，不推状态；fork 执行在子代理开始/结束时各推一条
  `status(stage=delegate)` 状态事件（经 clarify_channel 直投，不经 on_tool 映射，
  见 api_contract.md「delegate 状态阶段」）
- fork 结果为纯文本，**无 [n] 引用**：引用仍只指向主 agent 自身检索来源（tool_contexts），
  不指向子代理产出
- delegate 轮放宽迭代上限：`_delegate_used` 置位后 route_agent 上限
  +`MAX_DELEGATE_BONUS`（整合余量，agent_node.py:131-155）
- 实现与术语：src/agents/skills/（SkillRecord/SkillLoader/SkillRegistry/
  SkillExecutor/make_delegate_task），术语见 glossary.md「技能委派」

### 链路 2a：绑 KB 问答链（RAG）

- 触发条件：`kb_id` 非空（前端已绑定知识库），`ctx.kb_bound=True`。
- 与 2b 的差异：prompt 允许检索；`retrieve_kb` 真正执行 KB 混合检索；verify 走态 B 校验链
  （年份完整性 → 联网询问/决策 → KB 溯源护栏，无在线忠实度 judge）；SSE 主状态为
  `retrieve` 检索阶段，引用 `kind=kb`（检索不足联网补数据时混入 `kind=web`）。

```
agent（bind_tools）
  ├ retrieve_kb：hybrid 混合检索 + rerank 精排 → ctx.tool_contexts（kind=kb）
  │   query 含时间词且 TEMPORAL_PARSE_ENABLED 时先 parse_temporal →
  │   ctx.temporal_years / missing_years（完整性校验数据源，rag_tools.py:109-122）
  ├ search_web：KB 检索不达标时 agent 自主降级联网（web_guided=False → TO_WEB 信号）；
  │   下述 verify 指派补年份（ctx.web_guided=True）属正常步骤、不发缺陷信号
  ├ ask_user：关键实体缺失时澄清（SSEAskUserEvent，前端 composer 接管输入）
  └ 无 tool_calls → agent_finalize（提取末条 AIMessage → answer，读入 tool_contexts）

verify（态 B，verify/node.py:42-62）按序：
  A. completeness_check（checks.py:22）：required=ctx.temporal_years 与答案实际年份比对
     （required 为空 = 时间解析未触发 → 跳过）
       缺失非空 → decide_missing_web（regen_decision.py:49）并返回其结果，本轮 verify 结束：
         _ask_web_confirm 询问"缺失年份是否联网补充"（SSE ask_user id=web_confirm）
           → 拒绝/超时/槽被占：答案追加"仅覆盖 XX 年"注记 → 直通 format
           → 确认：看 agent 上一轮 search_web queries —— 带全仍缺 → "网络已穷尽"注记直通；
             未调过/带漏 → 注入 VERIFY_GUIDANCE_PROMPT（带漏再加 VERIFY_HINT_PROMPT）→ regen
         （保险丝 _verify_regenerations ≥ MAX_VERIFY_REGENERATIONS=2 → 注记直通，防无限往返）
  B. 无缺失 → kb_citation_guardrail（guardrails.py:137）：有 KB context 但答案无 [n]
       且非拒答/知识库未覆盖 → 注入 KB 溯源指引 → regen 一次（不占 verify 保险丝）
  C. 护栏通过 → 直通 format（在线忠实度 judge 已移除，质量评估转离线另行规划）
→ 通过 → format（nodes.py，[n] → citations 去重，kind=kb/web）
```

关键代码：态 B 分派 src/agents/graph/verify/node.py:42-62；联网询问
src/agents/graph/verify/ask_confirm.py:17-72；决策化 src/agents/graph/verify/regen_decision.py:49-168；
KB 溯源护栏 src/agents/graph/verify/guardrails.py:137-179；检索空结果/reretrieve 缺陷信号
src/agents/tools/rag_tools.py:192-204；拒答 → SSEAbstentionEvent（含 abstain_after_retrieve
信号）src/services/agent_service.py:487-509。

### 链路 2b：未绑 KB 纯对话链

- 触发条件：`kb_id` 为空串（会话未绑定知识库），`ctx.kb_bound=False`。
- 与 2a 的差异：prompt 追加 `KB_UNBOUND_SYSTEM_PROMPT` 明令禁止检索（prompts.py:50）；
  `retrieve_kb` 即便被调也返回空，不产出 kind=kb 上下文；verify 仅走态 A 联网引用引导，
  无年份完整性/联网询问；SSE 主状态为 `web_search` 联网阶段，引用仅 `kind=web`
  （未联网的纯闲聊则无引用）。

```
agent（bind_tools，同一工具列表）
  ├ retrieve_kb：禁止调用（prompt 软引导 + kb_id 空返回空硬保证，双保险）
  ├ search_web：Tavily 并行搜索 + 正文抽取 → ctx.tool_contexts（kind=web），
  │   每轮最多 WEB_SEARCH_PER_TURN_LIMIT 次调用（web_tools.py:61-67）
  ├ ask_user：关键实体缺失时澄清（SSEAskUserEvent）
  └ 无 tool_calls → agent_finalize

verify（态 A，verify/node.py:35-41）：
  web_citation_guard（guardrails.py:68-104）：
    本轮调过 search_web 但答案无 [n] 引用（且未引导过 / 未达保险丝）
      → 注入"请为联网引用标注来源编号"SystemMessage → regen 一次
    其余（未联网 / 已带 [n] / 已引导过 / 保险丝耗尽）→ 直通 format
→ format（引用 kind=web）→ END
```

关键代码：态 A 分派 src/agents/graph/verify/node.py:35-41；联网引用引导
src/agents/graph/verify/guardrails.py:68-104；未绑 KB 禁检索指令
src/agents/graph/agent_node.py:68-74 与 src/config/prompts.py:50（KB_UNBOUND_SYSTEM_PROMPT）；
空 kb_id 不检索 src/agents/tools/rag_tools.py:127-130；web 结果写入
src/agents/tools/web_tools.py:131-141。

### 字段级生产-消费矩阵（StateGraph）

两条链路共享同一拓扑与节点实现，矩阵不区分链路。节点通过共享 `AgentState` 间接通信：
每个节点消费若干字段、生产若干字段，字段 key 定义于 `src/agents/graph/state.py`（`format`
节点名取 `LangGraphNode.Format.NAME`，其余节点在 `workflow.build_graph` 以字符串注册）。
字段名生产侧（`agent_node.py` / `nodes.py` / `verify/`）与消费侧（`agent_service.py`）
必须一致，否则运行期报错或静默取空。

| 节点 | 消费字段 | 生产字段 |
|---|---|---|
| `agent` | `messages`, `_history`, `kb_id` | `messages`（LLM 输出含 tool_calls）, `_agent_iterations` |
| `tools` | `messages`（末条 tool_calls） | `messages`（ToolMessage 追加） |
| `agent_finalize` | `messages` | `answer`, `tool_contexts` |
| `verify` | `answer`, `kb_id`, `messages`（查上一轮 search_web 与指引查重） | `answer`, `_needs_regenerate`, `messages`（regen 指引 SystemMessage）, `_verify_regenerations` |
| `format` | `answer`, `tool_contexts` | `citations` |

工具（retrieve_kb / search_web / ask_user）不写 state：检索上下文累积到
`RequestContext.tool_contexts`（contextvar），由 `agent_finalize` 读入 `state.tool_contexts`；
ask_count / verify_ask_count / web_count / 澄清通道 / abort 信号均经 contextvar 传递。

SSE 消费侧按事件类型接线（`agent_service._convert_event`，src/services/agent_service.py:159）：
`on_chat_model_start`（节点 `agent`）→ "正在思考..."；`on_chat_model_stream`（节点 `agent`）
→ `token`（chunk 带 reasoning_content 时另发 `reasoning` 增量）；`on_tool_start/end` 按
工具名映射：`retrieve_kb` → `retrieve` 阶段（"正在检索相关文档.../检索完成..."）、
`search_web` → `web_search` 阶段（"正在联网搜索.../联网搜索完成..."）、`ask_user` 不发状态
（澄清卡由 ask_user/verify 经 clarify_channel 直投）；`on_chain_end`（`format`）→ citations。

## 链路 3：知识库管理

```
创建知识库 → MySQL get_or_create（名称去重）→ 返回 kb_id
列出知识库 → MySQL 查询 + 文档计数
删除知识库 → 软删文档 → ChromaDB 删集合 → 软删 KB 记录
```

入口: `POST /api/kbs` / `POST /api/kbs/list` / `POST /api/kbs/delete`

## 链路 4：会话管理 ★

```
列出会话 → MySQL 查询最近 50 条
查看消息 → MySQL 查询 session 消息历史
删除会话 → Redis 清理 → MySQL 事务删除 session + 消息
```

入口: `POST /api/sessions/list` / `POST /api/sessions/messages` / `POST /api/sessions/delete`

## 链路 5：认证

```
登录/注册 → MySQL 查用户 → 密码校验/自动注册 → Redis 存 token
校验 → Redis 查 token → 返回 user_id
登出 → Redis 删 token
匿名 → 生成 UUID → Cookie 持久化
```

入口: `POST /api/auth/login` / `POST /api/auth/verify` / `POST /api/auth/logout` / `POST /api/auth/anonymous`

## 链路 6：RAGAS 质量评估（CLI）

**子链路 6a — 测试集生成**：
```
MySQL 查元信息 → ChromaDB 取分块 → 脱敏 → 构建 KnowledgeGraph
→ transforms → 生成测试集 QA → 保存 JSON
```
命令: `python -m src.cli.eval_ragas --kb-id xxx --generate --size 20`

**子链路 6b — 评估执行**：
```
加载测试集 JSON → 初始化 RAGChain → 对每条 QA 检索+生成
→ RAGAS 四指标评分 → 保存 CSV + Markdown → 写入 eval_report 表
```
命令: `python -m src.cli.eval_ragas --kb-id xxx`（加 `--gate` 启用质量门禁）

## 链路 7：分块质量评估（嵌入在链路 1 中）★

```
文档分块后 → 结构完整性评分 → 语义断裂率(SBR) → 粒度变异系数(CV)
→ 综合评分 → 写入 meta_info.eval（仅记录，不阻塞）
```

由 `CHUNK_EVAL_ENABLED` 开关控制，`src/chunking/scorer.py` 的 `ChunkQualityScorer` 实现
（document_service.py 在分块后调用 evaluate）

## 链路 8：工具性链路

- **LLM 连通性测试**: `POST /api/llm/test` — 验证模型可用性
- **检索质量调试**: `python -m src.cli.check_retrieval --kb xxx --query "..."` — 纯检索+精排，不调 LLM
- **健康检查**: `GET /api/health` / `POST /api/config`

> ★ 标注的为关键链路

> 新增链路或修改节点字段矩阵（AgentState 生产-消费）时，同步更新本文件对应链路。
