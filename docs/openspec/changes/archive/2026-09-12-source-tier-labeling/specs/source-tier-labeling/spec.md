# source-tier-labeling Specification

## ADDED Requirements

### Requirement: 来源权威等级确定性定档

系统 SHALL 通过规则表为每个引用来源计算权威等级：KB 内部文档固定为 T0（内部文档，不走域名分级）；web 来源按域名匹配 `SOURCE_TIER_RULES` 定为 T1（官方一手）/T2（权威财经媒体）/T4（UGC）；`.gov.cn`/`.edu.cn` 域名模式 SHALL 升为 T1；未命中清单与模式者 SHALL 默认 T3（中性档）。定档 SHALL 为确定性输出——同一 URL 的定档不随模型采样变化，模型判断 SHALL NOT 改写已定档位。

#### Scenario: 官方域名命中 T1

- **WHEN** web 检索返回 url 为 `static.www.tencent.com` 的年报 PDF
- **THEN** 解析结果 SHALL 为 T1（官方一手）

#### Scenario: UGC 域名命中 T4

- **WHEN** web 检索返回 url 为 `zhuanlan.zhihu.com` 的文章
- **THEN** 解析结果 SHALL 为 T4（UGC）

#### Scenario: 未命中域名默认中性档

- **WHEN** web 检索返回 url 的域名不在规则表且不匹配任何模式
- **THEN** 解析结果 SHALL 为 T3（中性默认档），SHALL NOT 报错或丢弃

#### Scenario: KB 文档固定 T0

- **WHEN** 来源为知识库内部文档
- **THEN** tier SHALL 固定为 T0，不执行域名解析

#### Scenario: 相似域名不命中（域边界）

- **WHEN** web 检索返回 url 域名为 `evil-zhihu.com`（含规则域名子串但非其子域）
- **THEN** 解析结果 SHALL NOT 命中 `zhihu.com` 的规则，按未命中处理为 T3

#### Scenario: 最长后缀优先

- **WHEN** 规则表同时含 `guba.eastmoney.com`（T4）与 `eastmoney.com`（T2），url 域名为 `guba.eastmoney.com`
- **THEN** 解析结果 SHALL 命中最长后缀规则定档为 T4，SHALL NOT 被更泛规则覆盖

### Requirement: tier 全链路透传

tier SHALL 写入 `RAGContext`（三个构造点——KB 检索两处与 web 检索——均 SHALL 显式赋值，KB 为 T0，web 由解析函数产出，默认值 None 仅表示未定档）；返回给模型的 context 块文本 SHALL 携带档位标注；citation 数据结构（SSECitationEvent payload 与落库 sources）SHALL 携带 tier 字段。

#### Scenario: context 块携带档位标注

- **WHEN** 检索结果（KB 或 web）转换为喂给模型的 context 块
- **THEN** 块文本 SHALL 包含该来源的档位标注（KB 为「内部文档」，web 为对应档位标签）

#### Scenario: citation 携带 tier

- **WHEN** format_node 产出 citations
- **THEN** 每条 citation SHALL 携带其来源的 tier，并随 SSE citation 帧与 sources 落库

#### Scenario: 同 URL 重复入池去重后 tier 一致

- **WHEN** 多查询返回同一 url 多次入池（编号不同），format_node 按 (source, page) 去重
- **THEN** 保留条目的 tier SHALL 为该 url 的确定性定档（同 URL 定档恒一致），SHALL NOT 引入 tier 合并逻辑

### Requirement: 非法引用编号观测信号

format_node 对超出编号池范围的引用编号 SHALL 记录 `retrieval_signal: signal=invalid_citation`（含被忽略的编号值），不再静默忽略；同一编号重复出现 SHALL 聚合计数（count），SHALL NOT 逐次产出多条信号。

#### Scenario: 幻觉编号被观测

- **WHEN** 答案中出现 [99] 而编号池仅 1-13
- **THEN** 该编号 SHALL 不进入 citations（既有行为不变），且 SHALL 产出 invalid_citation 信号

#### Scenario: 重复编号聚合计数

- **WHEN** 答案中 [99] 出现 3 次（编号池不含 99）
- **THEN** 信号 SHALL 记录 count=3，SHALL NOT 产出 3 条独立信号

### Requirement: 候选规则信号离线聚合与人工审核

系统 SHALL 支持通过离线聚合 SQL 从 `conversation_history.sources` 统计**全部**域名的引用次数与跨会话数，作为种子清单成长的候选规则信号；是否命中规则表 SHALL 由人工在审核时对照 `SOURCE_TIER_RULES` 与拒绝清单排除，SHALL NOT 在 SQL 侧复刻解析逻辑；达阈值的未命中候选 SHALL 经人工审核（核对样本消息的引用上下文）后方可加入 `SOURCE_TIER_RULES`；被拒绝的域名 SHALL 记入文档化拒绝清单（negative cache，不建数据库表），供后续审核排除重复审批。SHALL NOT 使用 LLM 判定 tier 或自动升级档位。

#### Scenario: 高频域名进入候选审核

- **WHEN** 某域名被引用 ≥5 次且跨 ≥3 个会话
- **THEN** 离线聚合 SHALL 展示该域名的引用次数、跨会话数与样本消息定位；是否为「未命中」SHALL 由人工对照规则表与拒绝清单判定

#### Scenario: 人工审核通过后生效

- **WHEN** 审核者确认候选域名确为权威来源并将其加入 SOURCE_TIER_RULES
- **THEN** 重启后该域名 SHALL 由规则表确定性定档

#### Scenario: 无 LLM 定档

- **WHEN** 任意来源需要定档
- **THEN** 定档 SHALL 仅由规则表产出，SHALL NOT 调用 LLM 判断
