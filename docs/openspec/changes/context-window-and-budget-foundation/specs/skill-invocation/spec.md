## MODIFIED Requirements

### Requirement: skill 加载后持续生效

`/xxx` 触发 SHALL 将 skill 内容作为一条隐藏消息注入会话上下文。**触发粒度为消息级，生效范围为会话级**：后续轮次 SHALL 仍然看到该 skill 内容，直到**被历史窗口截断或新会话**。

⚠ **已知限制**：生效边界由 `_truncate_history`（`src/agents/graph/agent_node.py`）决定 —— 它按「最近 `HISTORY_MAX_TURNS`（=10）轮 + 绝对 token 预算 `HISTORY_TOKEN_BUDGET`（`src/config/const.py`）」截断，且**截断发生在注入消息被抽出之前**（先截断、后抽取）。因此：① 会话超过 10 轮后，注入消息作为"最旧的"被静默丢弃；② 一条 `inline` skill 会占掉相当比例的历史预算，加速自身被截。**系统当前没有上下文压缩机制**（该截断是丢弃，不是压缩）。

#### Scenario: 后续轮次仍受影响

- **WHEN** 用户第一条消息用 `/financial-statement-analyzer` 加载财务分析方法论，第二条消息未使用任何命令
- **THEN** 第二条消息仍受该 skill 内容影响（内容保留在会话上下文中）

#### Scenario: 注入内容对用户隐藏

- **WHEN** skill 内容注入会话上下文
- **THEN** 前端消息流不展示该注入消息（隐藏），但模型可见
