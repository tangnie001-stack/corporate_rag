# chat-temperature-policy Design

## Context

主 agent 模型在 AgentService 初始化时按 `LLM_TEMPERATURE=0.1` 构造，graph 编译一次后复用（workflow.py）。agent 节点已是"运行时按 state 每轮 per-call"模式：`model.astream(messages, extra_body={"enable_thinking": state.deep_thinking})`（agent_node.py）。langchain ChatOpenAI 请求 payload 会把显式传入的 `temperature` 合并进 `_default_params`，仓库已有 `llm.ainvoke([...], temperature=0)` 先例（src/rag/temporal.py:128）。因此分档无需双实例或重建图。

## Goals / Non-Goals

**Goals:**
- 主 agent 按 kb_id 分档温度：绑 KB 0.1 / 非 KB 0.6；同请求档位恒定
- 非 KB 默认值参数化（settings）

**Non-Goals:**
- 不改 fork 子代理采样（由 skill 层另行定义；不引入 skill temperature 字段）
- 不做按轮次/阶段分温（方案 B、writer 二段生成均不纳入）

## Decisions

### D1 实现：调用处直传 temperature kwarg
在 `agent_node` 每轮 `model.astream(...)` 处按 `state.kb_id` 追加 `temperature=`（kb 空→0.6，否则 0.1）。与 enable_thinking 同处，避免图/模型重建。
**备选**：双实例按 kb 缓存并替换 → 需触碰 graph 构建，复杂度高，否决。
**验证**：实现首步以流式冒烟断言请求 payload 含档位值（拦截 astream kwargs）。

### D2 同请求档位恒定
档位在请求入口按 kb_id 固定一次，全轮一致（spec 语义），不随轮次变化。

## Risks / Trade-offs

- **per-call temperature 不被 langchain 透传** → 首步冒烟验证；失败则回退在 `extra_body` 补发（DashScope 兼容模式支持）或双实例。
- **非 KB 温度升高对工具调用/联网决策的方差影响** → 工具调用为结构化输出，温度影响有限；联网事实答案偏多场景可把 0.6 下调（参数化预留）。
- **提温与 web 引用护栏的相互作用**：非 KB 提温可能降低 `[n]` 引用标记稳定性；态 A 既有 web_citation_guard 会引导补标 regen 兜底，护栏语义不变——实现冒烟时确认非 KB 联网答案引用横条仍正常。

## Migration Plan

- 单提交可回退；无数据迁移。
- 冒烟：KB/非 KB 各一题，确认档位命中与同请求一致；非 KB 回复语气对比 0.1 基线。

## Open Questions

- 无阻塞项；非 KB 默认 0.6 为定稿值。
