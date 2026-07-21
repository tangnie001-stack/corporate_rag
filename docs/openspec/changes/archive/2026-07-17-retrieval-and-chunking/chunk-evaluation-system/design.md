## Context

文档分块策略调整后，缺乏自动化的质量反馈。现有 `validate_chunks` 只检查 tiny_chunk 和 garbled 两种极端情况，无法评估分块的结构完整性、语义断裂度、粒度均匀性等核心质量维度。

RAGAS 评估已存在（`src/cli/eval_ragas.py`），但它是离线 CLI 工具，只输出 CSV/MD 报告到 `data/reports/`，结果不持久化到数据库，也无法与单个文档关联。

本项目以财务 PDF（表格密集、跨页、结构化）为核心文档类型，分块策略是表格保护+父子分块，评估体系需要优先覆盖**表格断裂**这个最高频的失效模式。

## Goals / Non-Goals

**Goals:**
- 上传文件后自动跑 3 个轻量指标（结构完整性 / SBR / 粒度CV），结果写入 document.meta_info
- 新增全局开关 CHUNK_EVAL_ENABLED，默认关闭，不影响现有行为
- 前端文档列表行展示整体评分（绿✓/红✗），点击弹窗展开明细
- RAGAS 评估结果持久化到 eval_report 表，KB 头部展示最新评分
- 模式 C：只记录不拦截，低分文件标红但不影响入库和检索

**Non-Goals:**
- 不自动补跑历史文件的评估（开关只影响新上传）
- 不做 RAGAS 定时采样（上线后再做）
- 不改解析器做字体级标题检测（用正则兜底）
- 不做 RAGAS 趋势图（第一期只展示最新一条）

## Decisions

### D1: 3 个指标的计算方式

| 指标 | 方法 | 阈值 |
|:----|:----|:-----|
| 结构完整性 | 正则检测 chunk 中 `|...|` 表格是否断裂、编号行/标题行是否跨chunk | `1 - broken/total` |
| SBR | 相邻 chunk 嵌入余弦相似度（用 `get_embeddings()`），低于 0.35 视为断裂 | `1 - broken/total`（标准化为越高越好） |
| 粒度 CV | 长度（token 数）变异系数 + 极端块检测（<50 或 >2×均值） | `score = 1 - min(cv, 1)` |

结构完整性权重最高（综合分中占 0.4），因为财务场景下表格断裂是最主要的失效模式。

### D2: 开关与集成位置

全局开关 CHUNK_EVAL_ENABLED，参考现有 LANGFUSE_ENABLE 模式。集成在 `_process_document_task` 中分块完成后、ChromaDB 入库前。评估不阻塞入库流程。

### D3: 存储设计

- **文档级（3 指标）**: `document.meta_info` JSON 列，零 schema 变更
- **知识库级（RAGAS）**: 新表 `eval_report`，包含四指标分数 + 逐条 QA 明细 (JSON) + 报告文件路径

### D4: RAGAS 与轻量指标的分工

轻量指标是文件级、毫秒级、零 LLM 依赖，每次上传自动跑。RAGAS 是 KB 级、分钟级、需 LLM，只在关键节点手动跑。两者互补不互斥。

### D5: 前端全量传 eval_detail

API 直接返回完整 JSON 到前端，避免额外 HTTP 请求。单个文件 eval_detail 几百字节-几 KB，对列表接口延迟影响可忽略。

## Risks / Trade-offs

| 风险 | 缓解措施 |
|:----|:--------|
| 正则检测 heading 误报率高（正文数字开头也被匹配） | A→C 兜底策略：效果差就降级为只做 table + clause |
| eval_detail JSON 膨胀（broken 预览很长） | preview 截取 50 字，控制 meta_info 体积 |
| SBR 调用 get_embeddings() 超时或失败 | 指标级降级：失败指标跳过，综合分只算有效指标 |
| 前端的弹窗交互实现复杂度高 | 提供模拟数据 + 交互说明文档 |
