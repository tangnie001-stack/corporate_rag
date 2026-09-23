# ADR-0012：trace 记录请求/回答原文并保留 30 天

- **Status**：Accepted
- **Date**：2026-09-23
- **Deciders**：用户（决策）；Claude（调研、实测与评审）
- **关系**：本 ADR 同时承载 ADR-0011 复查条件②（「Langfuse 首次被真正接入请求路径 → 重新评估降级决策」）的复评结论，属**确认**而非取代：接线后**维持 v2 降级** —— 接线不引 ClickHouse、不新增存储系统（trace 仍落复用的 PG 实例），且 Langfuse 仍在旁路（开关可关、后端不可达不影响对话）。ADR-0011 复查条件③（「接线 tracing 的那次变更必须一并落地 trace 保留/清理机制」）由本次变更一并落地（30 天保留期 + 清理 CLI），**已满足**。

## 背景与问题

langfuse-trace-wiring 变更首次把 Langfuse 真正接入请求路径：`src/services/agent_service.py` 的 `_run_generation` 与 `src/cli/eval_ragas.py` 的问题循环均加 `@observe`，节点级 `src/agents/graph/agent_node.py` 的 `agent_model` 亦加 `@observe`。这带来一个必须先定的取舍：**trace 里记不记 prompt / 回答原文，记了留多久。**

- 现状证据（均已落代码）：两端根函数用 `capture_input=False`（`agent_service.py:550`、`eval_ragas.py:105`），节点用 `capture_input=False` + `capture_output=False`（`agent_node.py:165-170`）。自动捕获被关掉，是因为根函数与节点入参含 `ctx` / `manager` / `graph` / `state` 等内部运行时对象，把这类对象序列化进 trace 既无意义又会撑大存储。内容仍**完整**，因为原文是两端**显式写入**的：`agent_node.py:245` 的 `update_current_observation(input=..., output=..., ...)`，以及 `agent_service.py:612`、`eval_ragas.py:128` 的 `update_current_trace(input=...)`。
- 保留期常量集中在 `src/config/const.py:319-324`：`PURGE_DEFAULT_RETENTION_DAYS = 30`、`PURGE_MIN_RETENTION_DAYS = 1`、`PURGE_MAX_DELETE_PER_RUN = 1000`、`PURGE_ALLOW_ENV_VAR = "LANGFUSE_PURGE_ALLOW"`，由 `src/cli/purge_langfuse_traces.py` 执行；该 CLI 须在 compose 网络内运行（`LANGFUSE_HOST` 为容器服务名 `langfuse-web:3000`，宿主机不可解析）。
- 触发本次决策的上游事件：这正是 ADR-0011 复查条件②所指的时点（Langfuse 首次进请求路径），而条件③要求本次一并落地保留/清理机制。

本 ADR 要回答的不是"性能/成本"——原文量级对本项目十万级的 trace 微不足道——而是**可回放性 vs 留存面**的取舍。

## 候选方案

| 方案 | 做法 | 代价 | 收益 |
|---|---|---|---|
| A 记原文 + 30 天保留（选中） | trace 显式写入 prompt / 检索上下文 / 回答原文；保留期 30 天，由清理 CLI 删除 | 新增一个持久原文副本，其保留期与对话记录不一致 | 链路可完整回放：一次问答的输入、检索到的上下文、模型回答都能在 UI 复现 |
| B 复用 `LLM_LOG_CONTENT` 开关 | 该开关关闭（默认）时不写原文，开启才写 | 语义耦合：一个"日志内容"开关同时决定 trace 的留存面 | 不新增配置项 |
| C 新增独立开关 `LANGFUSE_CAPTURE_CONTENT` | 专用于 trace 内容的开关，默认开 | 多一个语义相近的开关，配置面与文档面重复 | trace 内容可独立于日志开关控制 |

## 决策

**选 A：trace 记录 prompt / 回答原文（内容两端显式写入、自动捕获关闭），保留期 30 天。**

## 理由

关键一行：**旁路观测的价值全在"能回看那次到底喂了什么、答了什么"，而本项目的数据面已经不缺一份原文，所以决策落在"要不要多留一个副本、留多久"，而不是"要不要留"。**

- **不选 B**：`LLM_LOG_CONTENT`（`src/config/settings.py:53`，默认关，经 `src/models.py:164-171` 驱动 `LlmContentLoggingHandler`）是**日志**内容开关，语义是"把 prompt / 响应打进日志便于调试"。把 trace 内容绑到它上面，会让"开日志排查"顺带改变 trace 的留存面（关闭时 trace 无原文、无法回放），两个不同目的被一个开关耦合；且默认关闭意味着 tracing 接上了却回放不了，等于白接。
- **不选 C**：独立开关的收益与 B 同为"内容可关"，但它把同一个问题再配置化一次，并新增一个需与保留期/清理语义对齐的开关面。本期已接受记录原文，不需要一个开关来"可选地记"——真正需要"关内容"的那天是合规要求出现时（见复查触发条件），届时按合规口径设计，比现在预埋一个无人使用的开关更对症。
- **选 A 的正面依据**：原文已在同一 PostgreSQL 实例（对话历史 / 向量库）与镜像内 YAML 中存在，Langfuse Web UI 仅回环可达，记录原文**不引入新的数据类别**；换来的"可完整回放"是接线的核心收益，否则接线只剩耗时与事件序列。

## 后果

**正面**：

- 链路可完整回放：prompt、检索上下文、回答原文都可在 Langfuse UI 复现，tracing 接线具备实际观测价值。
- 内部运行时对象不入 trace：自动捕获关闭后，`ctx` / `manager` / `graph` / `state` 等不会被序列化进 trace。

**负面 / 接受的代价**：

- **新增一个持久原文副本。** 其保留期（30 天）与对话记录的保留期**不一致** → **trace 会活过对话被删之后**：用户删掉一次会话后，Langfuse 里那份原文仍在，直到 30 天保留期到期被清理 CLI 删除。
- 保留期以天为粒度、按时间清理，不做按主体删除（见「不解决的问题」）。

**不解决的问题**（明确列出，避免后人误以为这条 ADR 管了它）：

- **未做脱敏 / 遮蔽。** trace 记的是原文，未对 PII 或敏感词做替换/删除；一个 `mask` 回调留作将来（合规要求出现时再落地）。
- **未做按用户 / 按会话删除。** 清理 CLI 只按保留期批量删除，不支持"删除某个用户或某次会话的全部 trace"。

## 复查触发条件

1. **合规要求出现**（如要求 trace 内容脱敏、有限保留或按主体删除）→ 重估是否继续记原文、保留期是否调整。
2. **trace 量级超过既定阈值**（与 ADR-0011 复查条件①同一阈值）→ 重估保留期与 v3/v4 迁移。
3. **需要按用户删除数据** → 当前按天、按时间的清理无法满足，须重估清理机制（与「不解决的问题」呼应）。
