## ADDED Requirements

### Requirement: 精排分数观测（rerank done 分数字段）

`[retrieval] rerank done` 事件 SHALL 记录精排分数的分位信息：`score_max` / `score_min` / `score_p50`，并 SHALL 记录分数来源标记 `scored`（取值 `rerank` | `fallback`）。

不记录全量分数列表：该事件在每次 `retrieve_kb` 都会落盘，全量列表会显著放大日志体积，而分位信息已足以支撑阈值校准与"库内有无内容"的形态判别。

不另记 `score_top1`：精排结果按分数降序，`score_top1` 恒等于 `score_max`，同一事实记两个名字只会让消费方无法判断该信哪个。

**降级分数必须可辨**：精排调用失败时（`rerank failed`）系统回退为 `1 - distance` 造的分数，其量纲与语义不同于精排相关性分。这些样本 SHALL 以 `scored=fallback` 落盘，SHALL NOT 与 `scored=rerank` 的样本混入同一分布 —— 否则阈值校准与形态判别会被非精排分数污染。

精排**超时**路径（`rag_tools.py` 的 `except TimeoutError`）不在本要求范围内：它不落 `rerank done`，其 `1 - distance` 分数天然不进入该分布。

#### Scenario: 精排分数可见
- **WHEN** 一次 `rerank` 执行完成
- **THEN** 日志中的 `rerank done` 行 SHALL 出现 `score_max` / `score_min` / `score_p50` 与 `scored`

#### Scenario: 降级分数被标记
- **WHEN** 精排调用失败、结果回退为 `1 - distance`
- **THEN** 该 `rerank done` 行的 `scored` SHALL 为 `fallback`；消费这些分位数的判据 SHALL 只取 `scored=rerank` 的样本

#### Scenario: 空输入时不产生误导分数
- **WHEN** 精排输入为空（`rerank skip reason=empty_input`）
- **THEN** SHALL NOT 产生 `rerank done` 行，也不产生任何分数字段

### Requirement: 去重丢弃量观测（dedup done dropped）

内容级去重 SHALL 自落一条 `[retrieval] dedup done` 事件，记录丢弃条数 `dropped`（**精排后条数 − 去重后条数**）与保留条数 `kept`，使"取数天花板是否仍在生效"直接可见。

该统计 SHALL NOT 通过扩展 `retrieval.search` 的返回值来向外传递 —— `search` 的契约是"返回检索结果列表"，把去重统计透传出去会把编排关注点塞进检索层，并迫使所有调用方改签名。

#### Scenario: 去重丢弃量可见
- **WHEN** 一次内容级去重执行完成且丢弃了若干条候选
- **THEN** 日志 SHALL 出现 `dedup done` 行，`dropped` 等于精排后条数减去去重后条数，`kept` 等于去重后条数

#### Scenario: 无丢弃时记零
- **WHEN** 去重没有丢弃任何候选
- **THEN** `dropped=0` SHALL 出现在 `dedup done` 行中，而非字段缺失

#### Scenario: 检索入口的返回契约不变
- **WHEN** 调用 `retrieval.search`
- **THEN** 其返回值 SHALL 仍为检索结果列表，不因本要求而改为元组或携带统计的对象

## MODIFIED Requirements

### Requirement: 检索重放上下文日志

系统每次 `retrieve_kb` 执行 SHALL 落一条 `[retrieval] retrieve replay` 事件行，作为该 trace 的检索重放机器输入；行字段 SHALL 含 `query`（完整 + JSON 转义）/ `query_len` / `kb_id` / `iteration` / `top_k` / `hybrid` / `rerank`；trace_id 由日志框架自动注入。

`dedup_max_per_doc` 字段随 `RETRIEVAL_MAX_PER_DOC` 一并退出本行 —— 去重单位改为父块级后不存在"每文档保留条数"这一可配置维度，继续记录会输出一个无意义的值并污染重放对照。

#### Scenario: 按 trace 离线重放一次检索

- **WHEN** 拿到一条 trace_id，怀疑检索环节有问题
- **THEN** 从该 trace 的 `retrieve replay` 事件行可直接取出 query 全文、kb_id 与当时的 top_k 值，无需回查其它来源即可对当前 KB 重放该次检索，并把重放参数与"当时值"对照

### Requirement: 离线重放 CLI

系统 SHALL 提供重放命令（`python -m src.cli.replay_trace --trace <id>`）：读取该 trace 的日志（扫全部 `app_*.log`，**段位无关解析**：按 trace 子串过滤、取首个 ` - ` 之后为 message，兼容 `_LOG_FORMAT` 演进前后的新旧文件混存）→ 解析其 `retrieve replay` 事件（按 iteration 顺序）与行为信号 → 对当前 KB、当前配置重放检索并打印 top 片段（含来源与分数），并把事件行的当时参数并排对照、差异标注（drift 检测）。SHALL NOT 提供检索参数覆盖项：检索栈内部读模块常量不可覆盖，参数 A/B 属离线实验。trace 无检索轮时 SHALL 给出提示。输出 SHALL 标注"对当前 KB 重放（非历史快照）"。

#### Scenario: 一条命令定位检索问题

- **WHEN** 对某 trace 运行重放命令
- **THEN** 输出按 iteration 还原每次检索的命中片段，可据此分诊（召回漏 / 排序错 / 同文档上下文不足），无需手工抄录 query 与 kb_id

#### Scenario: 参数漂移对照

- **WHEN** 事件记录的检索参数（top_k/hybrid/rerank）与当前配置不同
- **THEN** CLI 重放输出并排显示"当时值 vs 本次值"并标注差异，提示检索行为变化可能来自配置漂移而非 query 本身
