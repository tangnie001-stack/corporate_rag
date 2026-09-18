## ADDED Requirements

### Requirement: BM25 索引缺失可见

当 BM25 支路因索引缺失或不可用而降级为空结果时，系统 SHALL 落一条显式事件，字段 SHALL 至少含 `kb_id`，使"混合检索名不副实"这一状态无需人工比对即可发现。

该要求针对的失败形态：索引缺失时 `search` 静默返回空、而融合后的事件照打 `hybrid done` —— 两者叠加使缺陷可以在生产上存活很久（实测半个月）。

事件级别 SHALL 区分"缺失"与"不可用"：缺失是**正常但少见**的状态（知识库尚无分块、或回填未做）→ `info`；不可用是**降级可恢复**的故障 → `warning`。事件前缀 SHALL 沿用检索层前缀 `[retrieval]`，SHALL NOT 为索引读写新建前缀。

#### Scenario: 索引缺失产生事件
- **WHEN** 一次检索中 BM25 支路因索引缺失而降级为空
- **THEN** 日志 SHALL 出现含 `kb_id` 的索引缺失事件，级别为 `info`，与该次检索的 trace 同链路

#### Scenario: 索引不可用产生事件并带原因
- **WHEN** BM25 索引存在但无法解析或无法使用
- **THEN** 日志 SHALL 出现该事件，含 `kb_id` 与失败原因，级别为 `warning`

#### Scenario: 损坏索引的移除动作可辨认
- **WHEN** 读取侧因索引确定性损坏而删除了该文件
- **THEN** 事件 SHALL 带有标明"已移除"的字段，使运维能区分"一直缺失"与"坏了并被清掉"

#### Scenario: 索引正常时不产生该事件
- **WHEN** BM25 索引正常加载并返回结果
- **THEN** SHALL NOT 产生索引缺失/不可用事件

#### Scenario: 事件前缀归属
- **WHEN** 检查新事件的落点
- **THEN** 前缀 SHALL 为 `[retrieval]`，且 `docs/agents/logging-rules.md` 的前缀主表 SHALL 已把该索引实现文件列入 `[retrieval]` 的归属

### Requirement: 有分块却无索引可见

仅靠检索侧无法区分"该知识库本来就无内容"与"该知识库有内容但索引丢了" —— 检索侧只持有 `kb_id`。因此系统 SHALL 提供一个不写盘的检查入口，列出**存在分块但没有可用索引**的知识库并记录 `warning`，使本次事故的真实形态（内容在、索引缺）以告警级别出现。

判定"存在分块" SHALL 使用**不创建 collection** 的只读计数途径。SHALL NOT 使用"取出全部分块"这类会经 `get_or_create_collection` 产生空 collection 副作用的途径 —— 否则本要求会以另一种形式复现它要消除的噪声（大量空 collection 被判为"有分块无索引"）。

判定索引"可用" SHALL 使用只读校验路径（见 `retrieval-quality` 的「索引可用性的只读校验路径」），SHALL NOT 复用带自愈删除的加载路径 —— 否则一次只读检查会当场删除损坏索引文件。

该检查同时是索引回填的目标集合来源 —— 回填 SHALL 只针对该检查列出的知识库执行，SHALL NOT 遍历全部知识库（遍历会经"不存在即创建"的 collection 获取路径产生空 collection 副作用）。

#### Scenario: 有分块但无索引被列出并告警
- **WHEN** 某知识库在向量库中已有分块，但其 BM25 索引不存在或不可用
- **THEN** 该知识库 SHALL 出现在检查结果中，且日志 SHALL 出现级别为 `warning` 的记录

#### Scenario: 有 collection 但无分块不被误报
- **WHEN** 某知识库存在 vector collection 但该 collection 内没有任何分块
- **THEN** 该知识库 SHALL NOT 出现在检查结果中，SHALL NOT 记录上述 warning

#### Scenario: 检查不产生写入副作用
- **WHEN** 执行该检查
- **THEN** SHALL NOT 新建 collection、SHALL NOT 写入或删除任何索引文件
- **AND** 即使存在损坏的索引文件，该文件 SHALL 仍然存在

### Requirement: 索引陈旧可见

当索引重建失败、而该知识库的旧索引仍然存在并继续被检索使用（用户会拿到过期结果）时，系统 SHALL 落一条 `warning` 事件，含 `kb_id` 与失败原因。

该要求针对的失败形态：重建路径当前捕获全部异常后仅记普通日志，且保留旧索引文件 —— 检索不报错、不降级，用户拿到的是与当前向量库不一致的过期结果，无任何信号。

#### Scenario: 重建失败时落事件
- **WHEN** 文档入库或删除触发的索引重建失败，旧索引文件仍在
- **THEN** 日志 SHALL 出现含 `kb_id` 与原因的 `warning` 事件

#### Scenario: 重建成功时不产生该事件
- **WHEN** 索引重建成功
- **THEN** SHALL NOT 产生索引陈旧事件
