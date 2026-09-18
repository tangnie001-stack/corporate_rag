## ADDED Requirements

### Requirement: 迁移等价性与词项命中探针

本变更同时发生两件性质不同的事：**存储替换**（不应改变行为）与**词法检索算法替换**（必然改变行为）。因此系统 SHALL 为两路提供**各自可判定**的验收判据，SHALL NOT 让两路共用同一判据。

**dense 路** SHALL 以**迁移等价性**验收：固定查询集（≥20 条，覆盖单知识库的中文 / 数值 / 时间三类），在同一份语料与同一批查询下比对替换前后的 top-k，**重合率 SHALL ≥ 0.9**。验收范围 SHALL 只覆盖单知识库路径。**该查询集 SHALL 落盘为可复现的清单**（不得只存在于任务描述中），否则验收不可复现。

**词法路** SHALL 以**词项命中探针**验收，判据固化如下：

- 探针词项 SHALL 从**原始分块正文**抽取，SHALL NOT 用分词后的检索文本或查询条件自判（那会让同一分词器既造索引又造标签，构成自我循环）
- 词项 SHALL 按文档频率筛选：保留 `2 ≤ df ≤ 0.1 × 语料分块数` 的词项（剔除 `df=1` 的无从判断项与高频的平凡通过项），长度 SHALL ≥ 2 字
- "含该词项的分块" SHALL 以**原始正文的字符串包含**判定，与分词器无关
- 横向比较不同分词配置时 SHALL 固定打分算法，或只比较与打分无关的量（词项可召回率）；SHALL NOT 同时改变分词器与打分算法（无法归因）
- 报告 SHALL 包含命中数与词项总数及词项清单，并声明**仅供相对比较**

端到端答案质量（RAGAS 类指标）**明确不在本变更的验收范围内**，SHALL 登记为语料到位后的独立评估活动。

#### Scenario: dense 迁移等价性可判定

- **WHEN** 对同一份语料、同一批固定查询，分别用替换前后两种存储执行单知识库 dense 检索
- **THEN** 两次 top-k 的重合率 SHALL ≥ 0.9；未达标 SHALL 阻止进入后续步骤，并先排查距离语义与过滤条件

#### Scenario: 词法命中率用于横向选型

- **WHEN** 用同一组词项探针比较候选分词配置
- **THEN** 每种配置的命中率 SHALL 被记录，配置 SHALL 依据实测选定而非推断

#### Scenario: 探针不自我循环

- **WHEN** 检查探针的词项来源与命中判定
- **THEN** 两者 SHALL 都基于原始正文，SHALL NOT 经过被测的分词器

#### Scenario: 探针覆盖被切碎与全单字查询

- **WHEN** 构造探针用例
- **THEN** SHALL 包含"查询经分词后词项被切碎或全部为单字"这一类用例，并断言该查询的词法路 SHALL NOT 因过滤而静默 0 命中（探针自带长度过滤会让这一类永远测不到，必须显式补）

#### Scenario: 小语料下不产出绝对质量结论

- **WHEN** 语料规模远小于目标规模（当前 176 个分块 / 5 个知识库）
- **THEN** 探针结果 SHALL 仅用于**相对比较**；SHALL NOT 被当作质量基线或发布判据。该限制 SHALL 在验收记录中显式写出（k=30~50 时单词项命中集合可能已占语料 17%–28%，多数词项会平凡通过）

#### Scenario: 端到端质量评估的遗留被显式登记

- **WHEN** 本变更验收通过
- **THEN** 端到端答案质量的回归判定 SHALL 作为显式遗留项登记，SHALL NOT 被默认为已通过

## MODIFIED Requirements

### Requirement: Rerank context passthrough

The system SHALL carry entity metadata from the `chunks` table through the rerank stage into `RAGContext.entities`.

#### Scenario: Entities survive rerank
- **WHEN** a chunk with `company`/`report_period` metadata is returned by rerank
- **THEN** the corresponding `RAGContext` SHALL expose those values via its `entities` dict
