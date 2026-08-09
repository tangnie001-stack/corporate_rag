## Context

当前 RAG 系统的 chunk metadata 只有技术字段（source/page/block_type/chunk_strategy/parent_content/doc_id/chunk_index/chunk_total/heading_path）。heading_path 是"预留但恒为空"的字段——parser 不产标题树。业务实体（公司名、报告期、证券代码、人名）完全缺失，导致：
- LLM 回答缺实体/时间锚点（如"本报告期""闫伟超职务"）
- RAGAS NLI 校验时上下文缺时间锚点，faithfulness 偏低（已排查确认根因是"2025年第一季度"限定词在表格 chunk 中缺失）

本变更在保留现有分块架构（parent_child/table_preserving/qa）的前提下，引入文档级实体抽取并注入 metadata，形成生产与 RAGAS 评估共用的上下文渲染。

关键事实（调研确认）：
- 参考项目 financial_rag-main 的标题上下文栈（ContextStack：6 条 HEADING_EXTRACTORS + 父级继承、兄弟零泄漏）可复用
- 现有 `_enrich_chunk_pages` 已用 `full_text.find(content)` 反推页码，同一机制可复用于标题段定位
- 现有 table_preserving 的 4 层表格保护（边界识别/孤儿合并/大表拆分/跨页合并）是财报场景的宝贵资产
- 实测：LLM（CLASSIFY_MODEL）能纠正规则误配（"世界领先的互联网科技公司"→"腾讯控股有限公司"），标题树仅 0.1~1.3k 字符可全量传入

## Goals / Non-Goals

**Goals:**
- 抽取文档级业务实体（company/report_period/sec_code/person/currency/report_type）并注入 chunk metadata
- 生产 prompt 与 RAGAS NLI 共用同一上下文渲染（`to_prompt_text`）
- 保留现有表格 4 层保护不倒退
- clarify 追问动态化（KB 候选替代硬编码）
- prompt 注入当前日期

**Non-Goals:**
- 不重写 chunker 为按树切块（方案 B 被否决——表格保护退化风险高）
- 不做 chunk 级实体抽取（实体是文档属性，文档级一次足够）
- 不做存量文档兼容迁移（用户确认直接清除重建）
- 不做实体 metadata 的检索过滤（仅渲染进 prompt，过滤留待后续）
- 不引入知识图谱（financial_rag 的 Neo4j 方案超出本次范围）

## Decisions

### D1: 引入 pymupdf4llm，替代现有 fitz 手写提取
- **选择**：`pymupdf4llm.to_markdown(doc, page_chunks=True, show_progress=False, write_images=False)`（参数对齐 financial_rag，但 `page_chunks=True`）
- **理由**：其输出带 `#/##/###` 标题层级，是标题栈规则层和标题段定位的数据基础；自带表格转 Markdown，替代手写 find_tables 逻辑
- **`page_chunks=True` 而非 False 的原因**：`False` 返回单字符串、丢页码；我们系统重度依赖 `page` 字段（format_node/agent_service 按 (source,page) 去重引用、to_citation/to_prompt_text 渲染"(第N页)"、api 文档分页查询）。`True` 返回 `list[dict]`，每页含 `metadata.page_number` + `text`，可拼出完整 Markdown 同时得到页码区间表
- **页码保留方案**：各页 `text` 拼接为 `full_text`（等价 False 结果，供 chunker + 标题栈）；页码区间表 `[(page_num, start_offset, end_offset)]` 用各页 text 长度累加计算，复用现有 `_enrich_chunk_pages` 反推页码机制
- **标题树载体**：`ParseResult` 新增 `heading_tree: list[tuple[int, str]]`（level, heading）字段，PDF 从 pymupdf4llm Markdown 提取、docx 用 python-docx heading 样式提取、txt 留空；`process_document` 直接消费，避免在 service 层重复解析
- **文件类型分流**：实体抽取按类型区分——PDF/docx 走完整三层（文件名+标题栈+LLM），txt 走文件名+LLM 兜底（无标题栈规则层）；docx 的标题段定位可用（有 heading_tree），txt 跳过 heading_path 绑定
- **替代方案**：保留 fitz + 行级启发式标题识别 —— 实测对 tencent 年报误配严重（"世界领先的互联网科技公司"被当公司），不可靠
- **注意**：现有 HEADER_FOOTER_MARGIN 页眉页脚过滤、MIN_TEXT_CHARS 扫描件判定逻辑需要适配；扫描件检测保留（pymupdf4llm 对扫描件输出空/极少文本）

### D2: 方案 C —— 标题段定位，而非按树切块
- **选择**：现有 chunker 分块逻辑不变（表格保护保留），分块后通过标题段区间表 + `full_text.find(content)` 反推 `heading_path`，只写 metadata 不改 content
- **理由**：对比发现 financial_rag 的 `_split_financial_table`（L204-213）只是打 block_type 标记，**不做真实表格原子化**；按树切块会让现有 4 层表格保护退化，对财报场景是倒退
- **替代方案**：
  - 方案 A（纯文本 + 偏移反推）：可选，但语义破碎问题仍在
  - 方案 B（按树切块）：表格保护退化，否决
- **实现**：标题段区间表 `[(heading_path, start_offset, end_offset), ...]` 从标题树建立；反推逻辑与 `_enrich_chunk_pages` 合并成一次遍历

### D3: 三层实体抽取（文档级一次）
- **① 文件名正则**（跨文档锚点，最可靠）：`tencent_2024_annual.pdf` → company/year；`neusoft_2025_q1.pdf` → company/year/quarter
- **② 标题栈规则层**（正文确认）：复用 financial_rag ContextStack 的 6 条 HEADING_EXTRACTORS（year/quarter/report_type/company/currency）+ 补充 sec_code 正则（`证券代码[:：]?\s*(\d{6})`），父级继承、兄弟零泄漏
- **③ LLM 校验兜底**（三态开关 `ENTITY_LLM_FALLBACK`，模型 CLASSIFY_MODEL flash）：
  - `off`：纯规则，不调 LLM
  - `on`：每文档无条件走 LLM 兜底
  - `auto`（默认）：规则结果为空或缺关键类型时才走 LLM，最省成本
  - 输入 = 文件名 + 全量标题树（实测 0.1~1.3k 字符）+ 正文前 500~800 字符；输出 `{rule_correct, reason, entities}`；职责 = 纠正规则误配 + 补规则盲区（person/繁体年份/非常见公司）；失败降级用规则层结果，不阻塞入库
- **合并规则**：优先级 ③ > ② > ①；同名键高优先覆盖；LLM 只补不覆盖

### D4: 实体类型与承载
- **核心实体（3 类，规则层可靠提取 + 渲染进 prompt）**：`company` / `report_period` / `sec_code`
  - 均为文档级属性，直接支撑 faithfulness 锚点（时间锚点根因正是 report_period 缺失）
  - 实测：含 person 的 Q1 faithfulness 已 1.0，说明 person 对检索/生成无增量价值
- **可选实体（3 类，LLM 兜底运行时顺带返回，不专门触发，不承诺渲染优先级）**：`person` / `currency` / `report_type`
  - person：无知识图谱/人物关系消费场景，检索无用（Q1 已 1.0），YAGNI
  - currency：单币种财报正文自带（"单位：人民币"），用户问币种概率低
  - report_type：本质是章节归属属性而非文档级实体，消费场景是检索过滤（Non-Goal），应走 heading_path 链路
- **标题栈规则层仍提取 year/quarter/report_type/currency**（正则免费，照收不误），但只有核心 3 类进入 `ENTITY_RENDER_ORDER` 渲染，其余作为 meta_info 补充字段
- **RAGContext 用开放 `entities: dict`**（不写死字段）：实体字段集开放（未来可能加 auditor/exchange 等），写死会导致频繁改 dataclass + 透传 + 渲染 + 测试；dict 访问收口在 `to_prompt_text`，不违反"优先 dataclass"规范（RAGContext 是透传/渲染层，非业务数据结构）
- **渲染**：按 `ENTITY_RENDER_ORDER`（核心 3 类）顺序渲染存在的实体，避免 dict 插入序不稳定

### D5: 落库与消费
- chunk.metadata 增实体字段 → ChromaDB（含 heading_path 接真值）
- document.meta_info 聚合 `{"entities": {...}}` → MySQL（clarify 数据源）
- rerank_results 从 chunk.metadata 透传 entities → RAGContext.entities
- `to_prompt_text()` 渲染：`来源: xxx (第N页) 公司: xxx 期间: xxx\n内容: ...`

### D6: 存量重建
- 不做兼容迁移（ChromaDB collection.update 方案否决）
- 清除现有 ChromaDB collection + document 表记录，重新入库
- 所有文档走新解析链路（pymupdf4llm + 实体抽取）

### D7: clarify 动态化 + 当前日期
- `document.meta_info` 聚合 KB 候选 → `CLASSIFIER_USER_TEMPLATE` 注入 `{kb_entities}` 占位符（query_router.py）
- `SUGGESTIONS_MAP` 硬编码 → 动态 KB 候选（agent_service.py:181）
- `PromptManager.get_system_prompt()` 追加今日日期（Langfuse prompt 也统一追加）

## Risks / Trade-offs

- [pymupdf4llm 表格 Markdown 与 table_preserving 的 TABLE_PATTERN 兼容性未知] → 实施时先用 neusoft/tencent 样本实测，不兼容则适配 TABLE_PATTERN
- [full_text 换源后 `_enrich_chunk_pages` 的 find 定位准确性] → 反推逻辑合并时加单元测试覆盖
- [pymupdf 1.28.2 升级对现有扫描件检测/文本提取回归] → 升级后跑解析回归
- [LLM 校验可能"反向误改"（规则对时 LLM 改错）] → prompt 约束"规则结果与原文一致时保留规则值，仅当规则值在原文无依据或明显不是实体时才纠正"
- [实体注入后 prompt 变长，token 成本略增] → 实体仅数十字，可忽略
- [LLM 兜底每文档一次 flash 调用] → 成本低，且开关可关闭退回纯规则

## Migration Plan

1. 版本升级：pyproject.toml 更新 pymupdf==1.28.2 + 新增 pymupdf4llm==1.28.2
2. 解析层改造：pymupdf4llm 替代 + 标题树提取（先实测表格兼容性）
3. 实体抽取三层链路 + 落库双写
4. RAGContext 透传 + to_prompt_text 渲染（生产 = RAGAS NLI 对齐）
5. clarify 动态化 + 当前日期注入
6. 清除重建存量文档 → 全量回归评估验证

**Rollback**：回滚时恢复 pyproject 依赖版本 + 代码 git revert；存量文档需重建（不可逆，但数据可从 MinIO 原始文件恢复）。

## Open Questions

- pymupdf4llm 对扫描件的输出形态（is_scanned 判定逻辑具体如何适配）
- 实体抽取 prompt 的最终措辞（防反向误改的约束强度）
