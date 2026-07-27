## Context

项目中 5 个核心数据结构使用 TypedDict / 裸 dict 承载结构化数据，导致：

- `state.get("key", default)` 散落 4 个文件共 20+ 处
- `chunk["content"]` / `chunk["metadata"].get("page")` 散落 6+ 文件
- Chat 消息流 `msg["role"]` / `msg["content"]` 散落 3 层共 5 文件
- TokenUsage 两套不一致的 dict 形状，存在 hidden bug
- EvalReport 用 dict 传，马上转化成实体

统一改为 dataclass 后字段默认值集中、属性访问、IDE 可跳转。

## Goals / Non-Goals

**Goals:**
- AgentState 全部字段有默认值，调用方直接用 `state.key` 访问
- ChunkData 作为 chunker 标准返回类型，消除 `c["content"]` / `c["metadata"]`
- ChatMessage 作为消息标准类型，消除 `msg["role"]` / `msg["content"]`
- TokenUsage 统一两套形状，修复 `usage.get("total", 0)` 隐式 bug
- EvalReportEntity 直接作为方法参数类型

**Non-Goals:**
- 不改变任何业务逻辑或 API 接口
- 不改动 LangGraph 流程图拓扑
- 不改动前端 API 响应格式（Pydantic model 维持原样）

## Decisions

### 全部使用 `@dataclass` 统一

5 个结构全部使用 `@dataclass`，不引入 NamedTuple 或 Pydantic。
理由：所有结构在生命周期中都需要不同程度的可变性，dataclass 最灵活。

### ChunkData 的 metadata 字段

`ChunkData.metadata: dict` 保留为 dict——metadata 的 keys 取决于不同分块策略，定义 dataclass 会过度约束。

### TokenUsage 统一形状

```python
@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
```

两套来源（LLM 原生 + 估算）都映射到这个形状，`estimate_usage()` 返回 `TokenUsage` 而非 dict。

### grader_score 默认值

`grader_score: float = 0`，但节点运行时可能设为 None。
`route_by_grader` 用 `state.grader_score or 0` 兜底。
`grader_node` 中的 `score is not None` 保留（`grader.grade()` 本身返回 None）。

## Risks / Trade-offs

- **[低风险] dataclass 不支持下标 `state["key"]`** — 测试中已无此写法
- **[低风险] 节点返回 None 覆盖默认值** — `grader_score` / `or 0` 兜底
- **[低风险] ChunkData 替换 chunker 返回类型** — 影响 3 个 chunker 实现和 3 个消费文件，改动面较广
- **[无风险] LangGraph 兼容性** — 已用 `StateGraph(dataclass)` 验证通过
