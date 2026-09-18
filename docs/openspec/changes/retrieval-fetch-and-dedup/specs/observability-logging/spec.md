## ADDED Requirements

### Requirement: 取数分路观测（hybrid done 分路计数）

`[retrieval] hybrid done` 事件 SHALL 除融合后条数外，额外记录两路各自的贡献条数：`dense_count` 与 `bm25_count`。

理由是 BM25 支路可能在完全静默的情况下失效（索引缺失时 `search` 返回空且无任何日志），而融合后的 `result_count` 无法反映这一点 —— 该缺陷曾存活半个月无人发现。

#### Scenario: 分路计数可见
- **WHEN** 一次启用混合检索的 `retrieve_kb` 执行完成
- **THEN** 日志中的 `hybrid done` 行 SHALL 同时出现 `dense_count` 与 `bm25_count` 两个字段

#### Scenario: BM25 支路失效可判读
- **WHEN** 某知识库的 BM25 索引缺失导致该路返回空
- **THEN** `bm25_count=0` SHALL 出现在 `hybrid done` 行中，使失效可被直接判读

### Requirement: 精排分数观测（rerank done 分数字段）

`[retrieval] rerank done` 事件 SHALL 记录精排分数的分位信息：`score_top1` / `score_max` / `score_min` / `score_p50`。

不记录全量分数列表：该事件在每次 `retrieve_kb` 都会落盘，全量列表会显著放大日志体积，而分位信息已足以支撑阈值校准与"库内有无内容"的形态判别。

#### Scenario: 精排分数可见
- **WHEN** 一次 `rerank` 执行完成
- **THEN** 日志中的 `rerank done` 行 SHALL 出现 `score_top1` / `score_max` / `score_min` / `score_p50`

#### Scenario: 空输入时不产生误导分数
- **WHEN** 精排输入为空（`rerank skip reason=empty_input`）
- **THEN** SHALL NOT 产生 `rerank done` 行，也不产生任何分数字段

### Requirement: 去重丢弃量观测（dedup done dropped）

内容级去重 SHALL 自落一条 `[retrieval] dedup done` 事件，记录丢弃条数 `dropped`（融合后条数 − 去重后条数）与保留条数 `kept`，使"取数天花板是否仍在生效"直接可见。

该统计 SHALL NOT 通过扩展 `retrieval.search` 的返回值来向外传递 —— `search` 的契约是"返回检索结果列表"，把去重统计透传出去会把编排关注点塞进检索层，并迫使所有调用方改签名。

#### Scenario: 去重丢弃量可见
- **WHEN** 一次内容级去重执行完成且丢弃了若干条候选
- **THEN** 日志 SHALL 出现 `dedup done` 行，`dropped` 等于融合后条数减去去重后条数，`kept` 等于去重后条数

#### Scenario: 无丢弃时记零
- **WHEN** 去重没有丢弃任何候选
- **THEN** `dropped=0` SHALL 出现在 `dedup done` 行中，而非字段缺失

#### Scenario: 检索入口的返回契约不变
- **WHEN** 调用 `retrieval.search`
- **THEN** 其返回值 SHALL 仍为检索结果列表，不因本要求而改为元组或携带统计的对象
