# answer-grounding Specification

## Purpose
TBD - created by archiving change rag-citation-and-relevance-upgrade. Update Purpose after archive.
## Requirements
### Requirement: 引用标记输出

系统 SHALL 在生成 prompt 中要求 LLM 在引用参考文档内容时输出 `[n]` 标记，其中 n 为参考文档的序号（与 `format_context` 中 `[1] 来源: ...` 的编号一致）。

#### Scenario: LLM 输出引用标记
- **WHEN** LLM 生成回答并引用了参考文档中的内容
- **THEN** 回答文本 SHALL 包含与参考文档序号对应的 `[n]` 标记

#### Scenario: 未引用文档
- **WHEN** LLM 回答基于常识或明确未找到数据，未引用任何参考文档
- **THEN** 回答文本 SHALL 不包含任何 `[n]` 标记

### Requirement: 引用过滤

系统 SHALL 只向用户展示回答中实际出现的 `[n]` 标记对应的来源，未在回答中引用的 context 不得出现在引用列表中。

#### Scenario: 回答引用部分来源
- **WHEN** 精排后有 5 个 context，但回答只引用了其中 2 个（含 `[1]` 和 `[3]`）
- **THEN** 引用列表 SHALL 只包含这 2 个来源，其余 3 个不展示

#### Scenario: 非法引用标记
- **WHEN** 回答中出现的 `[n]` 标记超出参考文档编号范围（如共 5 篇文档但出现 `[9]`）
- **THEN** 系统 SHALL 忽略该非法标记，不得展示对应来源

### Requirement: 拒答时不展示引用

当回答明确表示未在文档中找到相关数据（含"未在文档中找到"等拒答语）**且不含任何 `[n]` 引用标记**时，系统 SHALL 不展示任何引用来源。当回答含 `[n]` 引用标记（如 web 兜底回答"该问题不在当前知识库范围内，以下是网络搜索结果[1]…"），即使措辞混入拒答语，SHALL 保留对应引用。

#### Scenario: 拒答回答无引用
- **WHEN** 回答包含"未在文档中找到相关数据"且无任何 `[n]` 引用标记
- **THEN** 引用列表 SHALL 为空

#### Scenario: 混入拒答语但带引用标记
- **WHEN** 回答为"未在文档中找到该信息，该问题不在当前知识库范围内，以下是网络搜索结果[1]…"
- **THEN** 引用列表 SHALL 保留 `[1]` 对应的来源，不被拒答检测误删

#### Scenario: web 兜底回答保留引用
- **WHEN** 回答包含"该问题不在当前知识库范围内"且引用了 search_web 结果
- **THEN** 引用列表 SHALL 包含对应的网络来源

### Requirement: 引用展示去重

系统 SHALL 按（source, page）对引用去重，同一文档同一页只展示一次。

#### Scenario: 同源多块引用去重
- **WHEN** 两个 context 来自同一文档同一页
- **THEN** 引用列表 SHALL 只展示一条该来源记录

### Requirement: 引用来源类型标记

引用列表中的每条引用 SHALL 携带 `kind` 字段，标识来源类型：`kb`（知识库文档）或 `web`（网络搜索）。

#### Scenario: KB 引用标记为 kb
- **WHEN** 引用来自 retrieve_kb 的文档结果
- **THEN** 该引用的 kind SHALL 为 "kb"

#### Scenario: 网络引用标记为 web
- **WHEN** 引用来自 search_web 的网络结果
- **THEN** 该引用的 kind SHALL 为 "web"，source 为网页 URL
