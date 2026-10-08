## Context

调研大图见 `docs/context-memory-research.md`；方向决策见 `docs/adr/0017-context-ladder-and-memory-scope.md`。本 change 经独立架构评审（判 `Request changes`）后**收窄**为四层阶梯的**地基三件**。

当前状态（已核实）：

- `src/agents/graph/agent_node.py:28-57`：`_truncate_history(history, max_turns=HISTORY_MAX_TURNS, token_ratio=HISTORY_TOKEN_RATIO, context_window=8000)`；调用点 `:119` 不传 `context_window` ⇒ 预算 = 0.3×8000 = **2400 token**。
- `src/config/settings.py:92-93` `MODEL_CONTEXT_WINDOW_TOKENS`（默认 32768）：**唯一使用点** `src/rag/prompt.py:185,189`（段落占比告警）。
- 运行时模型 `.env` `LLM_MODEL=qwen3.8-2.4t-a95b`（`CLASSIFY_MODEL` 同），实测**输入**上限 **983,616**（探针见 `docs/context-memory-research.md` §八）。
- 计数粗估**两处**：`agent_node.py:53,56`、`src/infra/llm/token_usage.py:20-21`。
- 既有测试**硬编码了口径**：`tests/agents/graph/test_history_window.py:17,30,44` 显式传 `token_ratio=0.3, context_window=8000`；`tests/agents/graph/test_loop_middleware.py:550` 断言 `usage["input"] == 9`（依赖 `len//2`）。
- 既有 middleware 四件套全为**模型调用/节点后置**钩子（`src/agents/graph/middleware.py`），本轮**不新增钩子类型**。

### 评审后的范围决定（关键）

| 评审结论 | 本设计的处置 |
|---|---|
| **Blocker**：工具钩子在结果产生瞬间调用，结果恒为「当前轮」，而唯一可能超长的工具结果恰在当前轮 ⇒ L0 截断是空操作（「该截断的集合 == 被保护的集合」） | **移除 L0 截断与工具钩子**，本阶段只做**度量**，且度量挂在既有**模型调用**钩子上 |
| **Blocker**：「阈值 = 输入上限 − reserve」与「默认预算显著小于上限」矛盾，上限数值不影响 Phase A 结果 | **移除窗口解析**，改为**单一保守绝对预算**（与窗口解耦），判据变为可直接断言 |
| **Important**：迁移 step 标「无行为变化」不实（CJK 下 `len//2` 与 tiktoken 相差 2.57×） | 如实标为**行为变化**并纳入回归 |
| **Important**：测试破坏面被低估 | 在 Impact/tasks 中**点名**两个测试的改法 |
| **Important**：`provider 元数据`层无取数来源、与「唯一事实来源」冲突 | 该层随窗口解析一并**移出**本变更 |
| **Important**：探针把「下界当上限」 | 探针**移出**本变更（Phase B 再做，届时需支持 `≥N / unprobed` 语义） |
| **Important**：治理失败路径未定义 | 明确定义**度量/观测失败降级**（warning 吞掉，不影响请求），对齐 `rules.md` 与 `AgentSpanMiddleware` 既有做法 |

## Goals / Non-Goals

**Goals:**

- 历史预算口径唯一且可断言：**集中的绝对 token 量** + 轮数粗筛；删除写死默认值与比例主口径。
- token 计数统一到分词器（消除**两处** `len//2`）。
- 轮内累积**被度量**并产生可观测信号。
- 同步修正两个受影响的既有测试。

**Non-Goals:**

- 不做 per-model 窗口注册表/解析器/探针（Phase B）。
- 不做工具结果截断/去重/占位，不新增工具调用钩子（Phase B/D）。
- 不做摘要压缩（Phase B）、checkpointer 短期记忆统一（Phase B）、长期记忆 Store（Phase C）。
- 不修「触顶 → 空回答」（F-37）。
- 不改 prompt 五段结构；不改 API 契约。

## Decisions

### D1 历史预算改为单一保守绝对值，与模型窗口解耦（解 F2）

- 新增 `HISTORY_TOKEN_BUDGET`（**绝对** token 量，集中 `src/config/const.py`），作为历史裁剪的唯一预算口径。
- `_truncate_history` 改为按该绝对预算细裁；**删除** `context_window` 写死默认值；`HISTORY_MAX_TURNS` 保留为**轮数粗筛**（先按轮数取近段，再按绝对预算细裁）。
- **废弃** `HISTORY_TOKEN_RATIO` 的主口径地位。
- `MODEL_CONTEXT_WINDOW_TOKENS` **保留**现状（供 `prompt.py` 段落占比告警），并在注释中标注仍为**假设值**——本变更**不**基于它做预算。
- 理由：原「`上限 − reserve`」在 Phase A 中既不可证伪（上限 ~98 万使预算几乎必然远小于它，却又没有判据），又引入无取数来源的 provider 层。**单一绝对值**让判据变成一句话：**历史 token ≤ `HISTORY_TOKEN_BUDGET`**。

### D2 计数统一到 tiktoken，并如实承认这是行为变化（解 F3/F4）

- `tiktoken.get_encoding("cl100k_base")` 做**统一近似**，encoder 进程内缓存；定位是**相对一致与量级**，真值仍以 provider 的 `usage_metadata` 为准。
- 必须同时替换**两处**：`src/infra/llm/token_usage.py:20-21`、`src/agents/graph/agent_node.py:53,56`。
- **行为变化**（实测）：CJK 语料 `len//2` 与 tiktoken 相差约 **2.57×**（英文约 0.45×），故同样预算下**历史保留条数会改变**。这不是「无行为变化」的步骤，必须纳入回归。
- 受影响测试的改法：`test_history_window.py` 改按新口径（绝对预算）断言；`test_loop_middleware.py:550` 的 `== 9` 改为按新计数入口重算或改断言不变量（`>= 1`）。

### D3 轮内度量挂在既有模型调用钩子，不新增钩子类型（解 F1）

- 在既有 middleware（`wrap_model_call` 一族，与 `SystemMessagesMiddleware` 同机制）中，**模型调用前**度量当前消息列表的输入 token；达阈值即记 `used` / `limit` 日志。
- **不做截断、不做占位、不新增工具钩子**；因此不存在「当前轮 vs 已完成轮」的判定问题，也不触碰 `AgentSpanMiddleware` 末位守卫。
- 理由：Phase A 无摘要层，截断旧轮工具结果既无法判定「被引用」（`[n]` 在 `format` 阶段才定），也没有可回读替代（会致悬空引用）。**度量**是后续所有压缩决策的前提，且零风险。

### D4 度量/观测失败降级，不影响请求（解 F7）

- 度量与日志失败 SHALL 精确捕获 + 记 warning + **原样放行**，绝不因观测把一次成功调用变成错误（对齐 `docs/agents/rules.md` 的降级型处理与 `AgentSpanMiddleware` 既有做法）。

## Risks / Trade-offs

- **[`HISTORY_TOKEN_BUDGET` 初值缺乏实测标定]** → 取保守初值；单点可配；上线后按 Langfuse 的 token 记录校准（列入 Open Questions）。
- **[CJK 计数变化导致历史保留量下降]** → 已在 D2/Impact 显式登记；回归须覆盖中文多轮用例。
- **[既有测试断言失效]** → 已点名两个文件与具体断言行；属预期修正，不是回归。
- **[度量引入额外开销]** → 计数为纯本地运算（tiktoken + 缓存 encoder），无网络调用。

## Migration Plan

纯配置 + 代码，无 DB 迁移。三步，每步独立提交 + 全量回归：

1. **计数切 tiktoken**（两处）+ **修正受影响的既有测试**——行为变化（CJK 显著）。
2. **预算口径收口**：新增 `HISTORY_TOKEN_BUDGET`，`_truncate_history` 改按绝对值，废弃 `HISTORY_TOKEN_RATIO`，删除写死默认值——行为变化。
3. **轮内度量 + 降级**——新增观测，不改变内容。

**回滚**需区分两类（解 F8）：
- **阈值参数**（`HISTORY_TOKEN_BUDGET` 的取值）→ 改配置即可回滚。
- **口径与机制**（比例→绝对、计数切换、度量挂载）→ 须**回退提交**，不是「改配置值」。

## 已知遗留（本 change 不解决）

- **「触顶 → 空回答」**：既有 `AgentTurnBudget` 的 `jump_to="end"`（`middleware.py:150-151`）在工具未执行时可能产出空 answer，主 agent 无兜底文案。归属需求池 F-37。
- **Phase B 待做**：per-model 窗口注册表 + 解析器 + 探针（届时候选需支持 `≥N / unprobed` 语义与成本护栏）；摘要压缩及其挂载点验证；L0 工具结果治理（须先解决「已完成轮次」的可实现定义与 state 改写机制）。

## Open Questions

1. `HISTORY_TOKEN_BUDGET` 的初始取值？（建议从保守值起步，上线后按实测 token 分布标定）
2. `HISTORY_MAX_TURNS` 轮数粗筛的取值是否需与新预算联合回归？（新预算下 10 轮可能失效）
3. 输出上限与 provider 是否按 input+output 联合校验？（Phase B 摘要阈值前需补测；本变更不依赖）
