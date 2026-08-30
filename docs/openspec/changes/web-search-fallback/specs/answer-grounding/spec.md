# answer-grounding Specification (Delta)

## MODIFIED Requirements

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

## ADDED Requirements

### Requirement: 引用来源类型标记

引用列表中的每条引用 SHALL 携带 `kind` 字段，标识来源类型：`kb`（知识库文档）或 `web`（网络搜索）。

#### Scenario: KB 引用标记为 kb
- **WHEN** 引用来自 retrieve_kb 的文档结果
- **THEN** 该引用的 kind SHALL 为 "kb"

#### Scenario: 网络引用标记为 web
- **WHEN** 引用来自 search_web 的网络结果
- **THEN** 该引用的 kind SHALL 为 "web"，source 为网页 URL
