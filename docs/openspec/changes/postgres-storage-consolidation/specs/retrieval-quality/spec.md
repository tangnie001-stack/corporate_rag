## ADDED Requirements

### Requirement: 迁移等价性与词项命中探针

本变更同时发生两件性质不同的事：**存储替换**（不应改变行为）与**词法检索算法替换**（必然改变行为）。因此系统 SHALL 为两路提供**各自可判定**的验收判据，SHALL NOT 让两路共用同一判据。

**dense 路** SHALL 以**迁移等价性**验收：在同一份语料与同一批查询下，替换后的结果与替换前高度重合（差异只来自浮点与索引近似）。

**词法路** SHALL 以**词项命中探针**验收：从语料自身派生出一组有区分度的中文词项，逐个查询并断言"含该词项的分块"进入 top-k。

端到端答案质量（RAGAS 类指标）**明确不在本变更的验收范围内**，SHALL 登记为语料到位后的独立评估活动。

#### Scenario: dense 迁移等价性可判定

- **WHEN** 对同一份语料、同一批查询，分别用替换前后两种存储执行 dense 检索
- **THEN** 两次 top-k 的重合率 SHALL 达到设定阈值，未达标 SHALL 阻止进入后续步骤

#### Scenario: 词法命中率用于横向选型

- **WHEN** 用同一组词项探针比较候选分词配置（字符级 / 应用侧分词 / 可选扩展方案）
- **THEN** 每种配置的命中率 SHALL 被记录，配置 SHALL 依据实测选定而非推断

#### Scenario: 小语料下不产出绝对质量结论

- **WHEN** 语料规模远小于目标规模（当前 176 个分块 / 5 个知识库）
- **THEN** 探针结果 SHALL 仅用于**相对比较**，SHALL NOT 被当作质量基线或发布判据

#### Scenario: 端到端质量评估的遗留被显式登记

- **WHEN** 本变更验收通过
- **THEN** 端到端答案质量的回归判定 SHALL 作为显式遗留项登记，SHALL NOT 被默认为已通过

## MODIFIED Requirements

### Requirement: Rerank context passthrough

The system SHALL carry entity metadata from `chunks` table chunk metadata through the rerank stage into `RAGContext.entities`.

#### Scenario: Entities survive rerank
- **WHEN** a chunk with `company`/`report_period` metadata is returned by rerank
- **THEN** the corresponding `RAGContext` SHALL expose those values via its `entities` dict
