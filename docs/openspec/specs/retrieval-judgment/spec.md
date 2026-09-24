# retrieval-judgment Specification

## Purpose
TBD - created by archiving change web-search-fallback. Update Purpose after archive.
## Requirements
### Requirement: 一律先检索

非闲聊实质性问题 SHALL 一律先调用 `retrieve_kb` 检索知识库，根据检索结果判断能否回答，不预先猜测问题是否在知识库范围内。

#### Scenario: 非闲聊先检索
- **WHEN** 用户提出实质性查询
- **THEN** agent SHALL 调用 retrieve_kb，而不是凭记忆或常识直接作答

#### Scenario: 闲聊直接答
- **WHEN** 用户提出闲聊/问候
- **THEN** agent SHALL 直接回答，不调用检索工具

### Requirement: 内容判定标准

模型 SHALL 通过阅读检索结果内容判定相关性，判定标准为"chunk 内容含 query 至少一个核心实体才算相关"；rerank 分数 SHALL 仅用于排序，不做绝对阈值判定。

#### Scenario: 含核心实体判定相关
- **WHEN** 检索 chunk 内容含 query 的核心实体
- **THEN** 判定为相关，按文档内容作答并引用

#### Scenario: 无关内容判定不相关
- **WHEN** 检索 chunk 内容与 query 核心实体无关（如问"阿里云"返回股东持股内容）
- **THEN** 判定为不相关，走换词再检或联网兜底

### Requirement: 换词再检召回兜底

检索结果为空或全部明显不相关时，模型 SHALL 提炼核心实体换一种问法再次调用 `retrieve_kb`，第二枪 `top_k` 加大以降低"相关内容排在后面未被看到"的概率。

#### Scenario: 换词再检
- **WHEN** 第一次 retrieve_kb 结果为空或全部明显不相关
- **THEN** 模型 SHALL 提炼核心实体后再次调用 retrieve_kb（top_k 加大）

#### Scenario: 仍无结果转兜底
- **WHEN** 换词再检仍无相关结果
- **THEN** 判定为不在知识库范围内，调用 search_web 兜底；web 搜索关闭或失败时走纯拒答

### Requirement: 检索结果去重

检索结果 SHALL 做**内容级**去重：同一文档内同一父块下的多个 chunk 只保留**代表分最高**的一条。去重位置 SHALL 在 rerank **之后**、截断 `TOP_K_RERANK` 之前，输出按 `.score` 降序。

去重键 SHALL 同时包含文档标识与父块内容标识（`doc_id` + 父块内容的哈希）。系统 SHALL NOT 只按父块内容去重 —— 跨文档的样板文本可能逐字相同，只按内容去重会误折叠真实候选。去重单位 SHALL 是内容（父块），不是文档：同一文档的多个不同父块全部保留，系统 SHALL NOT 施加任何"每文档保留 N 条"的配额。

代表分的取值 SHALL 按每条候选最终的 `.score` 取每父块最高者（输入 `list[RAGContext]`）。分数语义按来源分三态：精排成功为 `relevance_score`；`rerank_results` 内部异常（`RERANK_FAILED`）回退为 `1 - distance`；精排超时（`rag_tools.py` 的 `except TimeoutError`）回退为 `1 - distance`。三种情形下按最终 `score` 取最高 SHALL 一致正确。

去重 SHALL 对**所有**检索返回路径生效，包括精排超时的降级路径（该路径不进入精排、按检索原始顺序返回，若不应用去重同一父块会被重复渲染）。

`retrieval.search` SHALL NOT 在检索入口做去重。

chunk 缺少父块内容时 SHALL 按自身保留（不去重）。

#### Scenario: 同父块多 chunk 只留一条
- **WHEN** 同一文档内出现同一父块下的多个 chunk
- **THEN** 精排后只保留精排分最高的一条，其余不进入最终上下文

#### Scenario: 保留的是最高分而非最先出现
- **WHEN** 同一父块下有多个 chunk，且其中一条精排分高于其余
- **THEN** SHALL 保留该最高分 chunk 作为该父块的代表，SHALL NOT 按融合顺序取最先出现的一条

#### Scenario: 跨文档相同文本不被折叠
- **WHEN** 两个不同文档各自有一个内容逐字相同的父块
- **THEN** 它们 SHALL 被视为两个不同的父块、各自保留，不得只留其一

#### Scenario: 同文档多父块全部保留
- **WHEN** 同一文档的多个不同父块同时被召回
- **THEN** 它们 SHALL 全部进入精排候选，不被每文档条数限制截断

#### Scenario: 去重发生在精排之后
- **WHEN** 一次检索完成精排
- **THEN** 去重 SHALL 在精排分数产出之后、截断 `TOP_K_RERANK` 之前执行，使被折叠条目的取舍依据是精排分而非融合名次

#### Scenario: 精排降级路径同样去重
- **WHEN** 精排超时（`rerank timeout`）走降级路径、按检索原始顺序返回
- **THEN** 该路径 SHALL 同样应用内容级去重，同一父块只保留**该父块内 `1 - distance` 最高**的一条，且 SHALL 产生 `dedup done` 事件（该路径无精排，`dropped` 取 **检索后条数 − 去重后条数**）

#### Scenario: 无父块内容时不去重
- **WHEN** 检索结果的 chunk 没有父块内容
- **THEN** 该 chunk SHALL 按自身保留，不参与内容级去重
