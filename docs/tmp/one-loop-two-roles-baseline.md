# one-loop-two-roles 改动前基线（Task 1）

> 本文件是重构「把主 agent 循环与 fork 子代理统一到同一装配入口（`create_agent`）」
> 的**改动前**基线快照，作为 Task 14 验收「对外行为逐字保持」的比对基准。
> 采集时点：**2026-09-29 09:00 CST**，分支 `feat/one-loop-two-roles`，
> HEAD `cf71e7a`（本任务提交前的最后一个提交）。
> 采集脚本 `/tmp/baseline_capture.py`（一次性，不入库）。

## 0. 采集环境（实测事实）

| 项 | 值 |
|---|---|
| 采集侧 | 宿主侧（`.venv/`），`POSTGRES_HOST=localhost`（Postgres 映射 `127.0.0.1:5432`） |
| 容器栈 | `corporate-rag-{app,postgres,redis,minio,nginx,langfuse-web}` 在跑（未重建/未重启） |
| 模型 | `LLM_MODEL=deepseek-v4-flash-0731`、`CLASSIFY_MODEL=deepseek-v4-flash-0731`（阿里云百炼） |
| 温度配置 | `LLM_TEMPERATURE=0.1`（绑 KB 档实际取值）、`NON_KB_MAIN_TEMPERATURE=0.6`（未绑 KB 档显式传参） |
| 迭代上限 | `MAX_AGENT_ITERATIONS=5`、`MAX_DELEGATE_BONUS`（`src/config/const.py`） |
| Langfuse | `LANGFUSE_ENABLE=true`；宿主侧脚本内覆写 `LANGFUSE_HOST=http://localhost:3000`（`.env` 原值为容器名 `langfuse-web`，宿主不可解析；未改 `.env`） |

## 1. Step 1 — 工作区与前置闸门

- `git branch --show-current` → `feat/one-loop-two-roles` ✅
- `git log --oneline -1` → `cf71e7a docs(plans): one-loop-two-roles 实施计划（14 任务，含基线比对与唯一装配静态断言）`
- `git status --short` → 干净（开工前）

## 2. Step 2 — 测试基线

| 门禁 | 命令 | 结果 |
|---|---|---|
| pytest | `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/ -q` | **1323 passed, 61 warnings in 57.80s**（exit 0） |
| ruff check | `ruff check .` | **All checks passed!**（exit 0） |
| pyright | `.venv/bin/pyright src/` | **0 errors, 0 warnings, 0 informations**（exit 0） |

- `ruff format --check .` 报「89 files would be reformatted」——这是**存量**状态（本任务未改任何
  `.py`），非本次引入。按记忆「全仓 ruff format 陷阱」，不执行全仓 `ruff format .`，避免污染 diff。

## 3. Step 3 — 五条日志 / SSE 序列 / citations 基线

### 3.1 采集方法

- 脚本 `/tmp/baseline_capture.py`，复用产品代码的 `AgentService.stream_chat` +
  `_run_generation`（与 HTTP 生产路径同一主循环），**不改产品代码**。
- 五条日志采集**不走 grep 文本**：包装 `src.core.logging.log_event` 记录结构化
  `(事件名, 字段 dict)` 后仍调用原实现；同时挂 loguru handler 采集渲染行，两者对照。
- 模型实收 kwargs：注入 LLM 代理（`bind_tools` → 代理 `astream`），记录**真实调用实收的 kwargs**。
- 每轮开跑前 `clear_history_async(session_id)` 清 Redis 历史，保证首轮 `history` 为空、可重跑。

### 3.2 固定问题集

| 标签 | kb_id | query |
|---|---|---|
| `kb_bound` | `ea84fb7235a941f9b64bcf4f5fa4b7f2`（东软集团 2025Q1 报告，121 chunks，真实存在） | 东软集团2025年第一季度报告的营业收入是多少？ |
| `kb_unbound` | `""`（未绑定） | 你好，请用一句话介绍你自己。 |

两问均**跑起主循环且未触顶**（`iteration limit` 在固定问题集内出现 0 次，见 3.6 补采）。

### 3.3 五条日志：条数与字段值

#### kb_bound（绑 KB）

| 日志 | 条数 | 字段值 |
|---|---|---|
| `prompt assembled` | 1 | `persona_source=domain kb_bound=true has_skills=true kb_domain=general tool_count=10 system_msgs=1 section_chars={"base":181,"runtime_contract":473,"sources":962,"tools":419,"output":443}` |
| `prompt messages` | 1 | `system_msgs=1 injected_msgs=0 history_msgs=0` |
| `iteration done` | 2 | `(iteration=1, msgs=2)`、`(iteration=2, msgs=4)` |
| `model turn` | 2 | 见下表 |
| `iteration limit` | 0 | 未触发（问题未触顶） |

`model turn` 字段值（kb_bound，两次采样）：

| # | model | usage_in | usage_out | usage_estimated | fallback | latency_ms | iteration | temperature | temp_source | kb_bound |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `deepseek-v4-flash-0731` | 1288 | 15 | true | false | 1598 | 1 | 0.1 | default | true |
| 2 | `deepseek-v4-flash-0731` | 4116 | 134 | true | false | 2329 | 2 | 0.1 | default | true |

#### kb_unbound（未绑 KB）

| 日志 | 条数 | 字段值 |
|---|---|---|
| `prompt assembled` | 1 | `persona_source=domain kb_bound=false has_skills=true kb_domain=general tool_count=10 system_msgs=2 section_chars={"base":181,"runtime_contract":473,"sources":618,"tools":419,"output":443}` |
| `prompt messages` | 1 | `system_msgs=2 injected_msgs=0 history_msgs=0` |
| `iteration done` | 1 | `(iteration=1, msgs=3)` |
| `model turn` | 1 | 见下表 |
| `iteration limit` | 0 | 未触发（问题未触顶） |

`model turn` 字段值（kb_unbound，两次采样）：

| # | model | usage_in | usage_out | usage_estimated | fallback | latency_ms | iteration | temperature | temp_source | kb_bound |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `deepseek-v4-flash-0731` | 1158 | 25 | true | false | 1304 | 1 | 0.6 | explicit | false |

> 口径说明：`usage_estimated=true` 表示流式聚合后 `usage_metadata` 缺失（当前
> `deepseek-v4-flash-0731` 流式不返回 token 计数），用量由 `estimate_usage` 文本长度估算，
> 故 `usage_in/usage_out` **不是模型真实计数**；`latency_ms` 为单轮墙钟。

### 3.4 两个温度档对照（模型实收 kwargs 实证）

代理记录到 `model.astream(...)` 的**真实 kwargs**：

| 档位 | 实收 kwargs | 结论 |
|---|---|---|
| 绑 KB（kb_bound=true） | `{"extra_body": {"enable_thinking": false}}` | **不带 `temperature` 键** ✅（沿用模型构造温度 `LLM_TEMPERATURE=0.1`） |
| 未绑 KB（kb_bound=false） | `{"extra_body": {"enable_thinking": false}, "temperature": 0.6}` | 显式传 `temperature=0.6` ✅ |

与 `model turn` 日志的 `temperature` / `temp_source` 一致（default↔0.1 / explicit↔0.6）。

### 3.5 SSE 事件序列（事件对象类型名 + 顺序）

事件对象类型来自 `_convert_event` 产出（`_run_generation` 写入 buffer 的事件）。
`token` 事件数随模型流式分块而变，故给**骨架**（连续 `token` 折叠为 `token×N`）。

| 标签 | 事件类型计数 | 骨架（两次采样） |
|---|---|---|
| kb_bound | `status=4, token≈89–110, citation=2–3` | 采样A：`status, status, status, status, token×87, citation, citation`<br>采样B：`status, token×8, status, status, status, token×110, citation, citation, citation` |
| kb_unbound | `status=1, token≈14–18` | `status, token×N`（N=14/18） |

**稳定不变量**：kb_bound 含 4 个 `status` + 至少 1 段 `token` + ≥1 个 `citation`；kb_unbound 含 1 个
`status` + 1 段 `token`、**无 `citation`**。
**随模型变的部分**：`token` 分块数、`status` 与 `token` 的**相对先后**、`citation` 条数。

### 3.6 补采：`iteration limit` 字段值（固定问题集外）

固定问题集刻意不触顶，故 `iteration limit` 出现 0 次。为取该日志**真实字段形状**，另跑一次
探针：同一图、同一 kb_id，把初始 state 的 `_max_agent_iterations` 压到 1，逼出该日志。

- 实采字段：`iteration limit query="东软集团2025年第一季度报告的营业收入和净利润分别是多少？" iteration=1`
- 说明：**探针是人为压低上限的合成场景**，只用于确认字段键与取值形态（`query`=原文、`iteration`=触发轮次），
  不代表固定问题集的正常行为。

### 3.7 最终 answer 的 `[n]` 标记与 citations 列表长度

| 标签 | `[n]` 标记 | citations 长度 | 说明 |
|---|---|---|---|
| kb_bound | `[1][2][3]`（另一次采样 `[1][2]`） | 3（另一次采样 2） | citations 全部 `kind=kb`、`source=neusoft_2025_q1.pdf`、`page∈{0,1,3}`、`tier=0`、`snippet_len=200` |
| kb_unbound | 无 | 0 | 未绑 KB 无检索，无引用 |

kb_bound 回答（采样，节选）：`**东软集团（600718）2025年第一季度营业收入为 1,849,804,893 元（约18.50亿元）**[1][2][3]`
kb_unbound 回答（采样）：`你好，我是企业知识库问答助手，可以帮你基于知识库或联网信息理解内容、查找资料，并完成报告、分析等各类请求。`

### 3.8 可重跑性 / 稳定性实测

同一脚本连跑 4 次（run1/2 首轮带历史，run3/4 已清历史）：

- **稳定（逐字一致）**：五条日志的**条数与字段键**、`prompt assembled` 的 `section_chars`、
  `prompt messages` 的 `system_msgs`（1 vs 2）、`model_astream_kwargs`、`model turn` 的
  `temperature/temp_source/kb_bound`。
- **不稳定（模型随机性，非代码）**：`token` 分块数、`answer_len`、`[n]` 标记集合、
  `citations` 条数、`usage_in/usage_out`（估算值）。
- 首轮若不清历史会读到上一轮写入的 user 消息（`history_msgs` 0→1），脚本已用
  `clear_history_async` 消除，保证可重跑。

> **对 Task 14 比对的影响**：模型输出逐字比对不可靠；比对应以**结构不变量**为准
> （日志条数/字段键、kwargs 温度分档、SSE 事件类型计数与有无 citation、citations 结构字段）。

## 4. Step 4 — CLI 入口基线（关键：该入口不建 `RequestContext`）

`src/cli/check_abstain.py:127` 与 `src/cli/eval_ragas.py:129` 均只把参数放进 `graph.ainvoke` 的输入 dict：

```python
# src/cli/check_abstain.py:127
final_state = await graph.ainvoke(
    {"kb_id": kb_id, "session_id": session_id, "query": sample.query,
     "trace_id": trace_id, "_history": [...]}
)

# src/cli/eval_ragas.py:129
final_state = await graph.ainvoke(
    {"kb_id": kb_id, "session_id": session_id, "query": query,
     "trace_id": trace_id, "_history": []}
)
```

- `grep -n "current_request_ctx" src/cli/check_abstain.py src/cli/eval_ragas.py` → **无命中**。
- `src/cli/eval_ragas.py:192` 只 `current_trace_id.set(...)`（第 220 行 reset），**不 set `current_request_ctx`**。
- 结论：**CLI 入口只把参数放进图输入、不建 ctx**；`current_request_ctx` 保持 `None`，
  `_initial_messages` 走 `ctx is None` 分支（`persona="" / has_skills=False / kb_domain="general" / known=set()`）。
  这正是 Task 8 中 `kb_id` **必须显式 seed 进 `create_agent` 子图 state** 的原因——CLI 路径没有 ctx 可回退。

## 5. 已知项（不属于本任务，照实记录）

- **定价与模型不匹配**：`MODEL_INPUT_PRICE_PER_TOKEN` / `MODEL_OUTPUT_PRICE_PER_TOKEN` 仍是
  qwen 时期数值，故 `model turn` 的成本字段（Langfuse 侧）**不代表新模型真实成本**。
  本任务未改定价、未重跑 `seed_langfuse_models`。
- **app 容器持旧 env**：容器仍在旧模型 env 下运行，本任务**走宿主侧**、未重建/重启任何容器。
- **`usage_estimated=true`**：见 3.3 口径说明，用量为估算值。

## 6. Task 14 比对用法

1. 重构后**重跑 `/tmp/baseline_capture.py`**（脚本本身不入库；如需复现，按 3.1 重建）。
2. 比对**结构不变量**（见 3.8「稳定」清单 + 3.5 稳定不变量），而非逐字回答。
3. 若某项与基线不符，先排除模型随机性（3.8「不稳定」清单），再判定为行为变更。

## 7. Task 14 验收结论（2026-09-29）

> 本段为 Task 14 验收结论追加，不改上方基线原值。采集时点 2026-09-29 CST，
> HEAD `69046d3`。方法同 §3.1，脚本为 `/tmp/baseline_capture.py` 的改编版
> （改动能捕获 bind_tools kwargs 与 max_turns=1 合成探针，详见报告）。

### 7.1 质量门禁（Step 1）

| 门禁 | 命令 | 结果 |
|---|---|---|
| pytest | `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/ -q` | **1339 passed, 61 warnings in 74.65s**（exit 0；基线 1323，多出的 16 条为重构期新增用例） |
| ruff check | `.venv/bin/ruff check .` | **All checks passed!**（exit 0） |
| pyright | `.venv/bin/pyright src/` | **0 errors, 0 warnings, 0 informations**（exit 0） |
| TIMING 埋点 | `grep -rn "TIMING" src/agents/graph/` | **空**（exit 1，埋点已删净） |
| agent_node.py 行数 | `wc -l src/agents/graph/agent_node.py` | **232 行**（< 400） |

- `ruff format --check .` 报「90 files would be reformatted」——存量状态（本任务未改 `.py`），
  与基线 89 的差异来自重构期新增文件，非本次引入；按记忆不执行全仓 `ruff format .`。

### 7.2 基线逐字比对（Step 2）——结构不变量逐项

**比对口径**：SSE 事件类型与计数 / 五条日志的条数与字段集及关键字段值 /
模型实收 kwargs 的温度分档 / citations 结构。不比对回答正文、token 分块数、
`answer_len`、`[n]` 位置、`usage_in|out`、`status` 与 `token` 交错顺序。

| 比对项 | 基线值 | 现值 | 结论 |
|---|---|---|---|
| kb_bound `prompt assembled` | 1 条，`system_msgs=1`、`sources=962`、`tool_count=10` | 逐字一致 | ✅ |
| kb_bound `prompt messages` | `system_msgs=1 injected_msgs=0 history_msgs=0` | 逐字一致 | ✅ |
| kb_bound `iteration done` | 2 条 `(1,msgs=2)`、`(2,msgs=4)` | 逐字一致 | ✅ |
| kb_bound `model turn` | 2 条，`temp=0.1` `temp_source=default` `kb_bound=true` | 逐字一致（iteration 1/2） | ✅ |
| kb_bound `iteration limit` | 0 条 | 0 条（基准 0、实收 0） | ✅ |
| kb_unbound `prompt assembled` | `system_msgs=2`、`sources=618` | 逐字一致 | ✅ |
| kb_unbound `prompt messages` | `system_msgs=2 injected_msgs=0 history_msgs=0` | 逐字一致 | ✅ |
| kb_unbound `iteration done` | 1 条 `(1,msgs=3)` | 逐字一致 | ✅ |
| kb_unbound `model turn` | `temp=0.6` `temp_source=explicit` `kb_bound=false` | 逐字一致 | ✅ |
| kb_unbound `iteration limit` | 0 条 | 0 条（基准 0、实收 0） | ✅ |
| kb_bound SSE 类型计数 | `status=4 token≈89–110 citation=2–3` | `status=4 token=115 citation=2` | ✅（token/citation 属随模型变） |
| kb_unbound SSE 类型计数 | `status=1 token≈14–18 citation=0` | `status=1 token=20 citation=0` | ✅（无 citation） |
| kb_bound citations 结构 | `kind=kb source=neusoft_2025_q1.pdf page∈{0,1,3} tier=0 snippet_len=200` | 同结构（`page∈{1,3}`、2–3 条） | ✅ |
| kb_unbound citations | 0 条 | 0 条 | ✅ |

**温度分档（模型实收 kwargs 实证）**——这是唯一需要改编脚本才能复现的项：
重构后温度经 `create_agent` 的 `bind_tools(**model_settings)` 施加，不再经 `astream(...)`，
故原脚本的 `astream` 代理采到空。改编脚本改记录 `bind_tools`/`bind` kwargs：

| 档位 | 实收 `bind_tools` kwargs | 结论 |
|---|---|---|
| 绑 KB | `{"tool_choice": null, "extra_body": {"enable_thinking": false}}` | **不带 `temperature` 键** ✅（与基线 astream 口径一致：绑 KB 不传温度） |
| 未绑 KB | `{"tool_choice": null, "extra_body": {"enable_thinking": false}, "temperature": 0.6}` | 显式 `temperature=0.6` ✅ |

与 `model turn` 日志的 `temperature`/`temp_source` 一致（default↔0.1 / explicit↔0.6）。
**唯一口径差异**：温度施加面从 `astream` 迁到 `bind_tools`（重构预期，非缺陷），
`tool_choice: null` 为 create_agent 新增的透传键（非温度相关）。

**`iteration limit` 字段值（合成探针）**：原探针引用的 `state._max_agent_iterations`
已被重构删除，改为装配 `build_agent(max_turns=1)` 逼出，实采
`iteration limit query="东软集团2025年第一季度报告的营业收入和净利润分别是多少？" iteration=1`
——字段键（`query`/`iteration`）与基线 3.6 逐字一致。✅

### 7.3 触顶复现（Step 3，真实路径）

用宽问题「腾讯这几年的业绩怎么样？请结合年报数据详细分析」在绑 KB 会话跑生产路径
（`_run_generation`），连跑 3 次均触发：

- `iteration limit` 日志：`query="…" iteration=5`（WARNING 级），字段集与基线一致 ✅
- 模型调用 5 次（`model turn` iteration 1–5），第 5 轮**正常收尾**（产出正文、未声明工具）
  仍产出告警 → 命中「上限轮正常收尾也产出告警」场景 ✅
- 工具执行 4 轮（iteration 1–4，每轮模型并行声明 2 次 `retrieve_kb`），第 5 轮不再执行 ✅
- **无任何提示性文案**注入：grep `已达|上限|无法回答|抱歉|调用上限` 零命中 ✅
- verify 照常收尾：`completeness check` → （其中两次触发重生成，regeneration 轮预算独立起算
  产生完整答案）→ `format done`，无异常 ✅
- 落库：`conversation_history` 行 `status=complete`、`content`=净化正文（非空，模型实际产出）
  ✅；落库路径（`src/api/`、`src/chat/`、`src/infra/db/`）经 `git diff cf71e7a..HEAD` 为空，
  **未被本重构改动**，落库语义与变更前一致 ✅

> 说明：基线冒烟（2026-09-25）观察到「触顶 → 空回答」。本验收 3 次触顶均在第 5 轮
> 产出正文（模型随机性），未复现「空回答」形态；「末条为含 tool_calls 的 AIMessage 且
> 允许空串」场景由 Task 5 单元测试 `test_hit_limit_skips_last_tool_and_keeps_tool_call_message`
> 覆盖（模型调用=上限次、工具执行=上限−1、末条含 tool_calls、允许空串），E2E 侧以
> 「正常收尾也产出告警」场景佐证。规格不变式「答案允许为空、不被替换为提示性文案或
> 工具返回内容」两场景均成立。

### 7.4 真实模型 E2E（Step 4）

| 场景 | 结果 |
|---|---|
| 绑 KB（kb_bound） | `model turn` `temp_source=default`/`temp=0.1`，`bind_tools` kwargs 无 `temperature` 键 ✅ |
| 未绑 KB（kb_unbound） | `temp_source=explicit`/`temp=0.6`，`bind_tools` kwargs 显式 `temperature=0.6` ✅ |
| 深度思考（deep_thinking=true） | `bind_tools` kwargs `extra_body={"enable_thinking": true}`，思考开关随调用链传入 ✅ |
| `/xxx` 直出（`/financial-statement-analyzer …`） | `direct_skill` 解析成功、`skill_action=fork`，子代理链路跑通：SSE 含 `delegate×507` 折叠事件、`[delegate] delegate model turn delegate_id=…`、产出完整分析 ✅ |
| 主 agent 委派轮（杜邦分析问题） | 模型自主调用 `delegate_task`：SSE 含 `delegate×274` 折叠事件、`delegate model turn` 2 次（`delegate_id=43b664bb`）、产出 7079 字 ✅ |
| Langfuse `agent_turn` observation | `observations` 表有 `name='agent_turn'`、`type='GENERATION'`，metadata 含 `iteration/temp_source/temperature/kb_bound/usage_estimated`，`model=deepseek-v4-flash-0731`，`prompt_tokens`/`completion_tokens` 齐全 ✅（`completion_start_time` 不设置，属已接受的 TTFB 退化，见 ADR-0016） |

### 7.5 结论与遗留

- **验收结论：通过**。五条日志的条数与字段集/关键字段值、SSE 事件类型与计数、
  温度分档、citations 结构、`iteration limit` 字段形状、触顶路径、真实模型 E2E
  全部与基线/规格一致；唯一「差异」是温度施加面从 `astream` 迁到 `bind_tools`
  （重构预期，非缺陷）。
- **遗留（非本次变更引入，照实记录）**：
  1. `src/services/agent_service.py` 的 `_silence_watchdog` + `src/agents/tools/web_tools.py`、
     `src/infra/search/tavily_client.py` 的 `[retrieval] TIMING` 埋点为 `c3cb56b`（早于基线
     `cf71e7a`）遗留的「316s 取证」埋点，仍在 `src/`（`src/agents/graph/` 已删净）。
    属独立清理项，不在 one-loop-two-roles 范围。
  2. `agent_node.py:162-163` 注释仍提「那些字段由下一批清理」的过期符号（Task 13 已登记 deferred）。
