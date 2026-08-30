## Why

当前 chunk metadata 只有技术字段（source/page/block_type/chunk_strategy 等），缺业务实体（公司名、报告期、证券代码、人名）。这导致：LLM 回答时缺少实体/时间锚点（如"本报告期""闫伟超职务"），RAGAS 评估的 NLI 校验因上下文缺时间锚点而 faithfulness 偏低。需要把文档级业务实体抽取并注入 chunk metadata，让生产 prompt 与 RAGAS NLI 共用同一渲染。

## What Changes

- **引入 pymupdf4llm 解析 PDF**：替代现有 fitz 手写文本/表格提取，`to_markdown()` 输出带 `#/##/###` 标题层级的 Markdown，并提取标题树供标题栈规则层和标题段定位使用。pymupdf 升级到 1.28.2，新增 pymupdf4llm==1.28.2。
- **文档级实体抽取（三层）**：①文件名正则 → company/year/quarter；②标题栈规则层（对齐 financial_rag ContextStack，HEADING_EXTRACTORS + sec_code 正则，父级继承、兄弟零泄漏）；③LLM 校验兜底（开关 `ENTITY_LLM_FALLBACK`，CLASSIFY_MODEL，输入 = 文件名 + 全量标题树 + 正文前 500~800 字符，输出 `{rule_correct, reason, entities}`）。
- **标题段定位（方案 C）**：现有 chunker（parent_child/table_preserving/qa）分块逻辑不变，表格 4 层保护保留；分块后通过标题段区间表 + `full_text.find()` 反推每个 chunk 的 `heading_path`，只写 metadata 不改 content。
- **实体落库**：实体注入每个 chunk.metadata（ChromaDB）+ 聚合存 `document.meta_info`（MySQL，clarify 数据源）。
- **检索消费**：`RAGContext` 增加开放 `entities: dict` 字段，`to_prompt_text()` 按 `ENTITY_RENDER_ORDER` 渲染存在的实体，生产 prompt 与 RAGAS NLI 共用同一渲染。
- **存量重建**：**BREAKING** — 不做兼容迁移，现有文档全部清除重建（删除 ChromaDB collection + document 表记录，重新入库）。
- **clarify 追问动态化**：`document.meta_info` 聚合 KB 候选，注入 `CLASSIFIER_USER_TEMPLATE` 的 `{kb_entities}` 占位符；`SUGGESTIONS_MAP` 硬编码（腾讯/阿里巴巴）替换为动态 KB 候选。
- **当前日期注入**：`PromptManager.get_system_prompt()` 追加今日日期，解决"本报告期/今年"锚定。

## Capabilities

### New Capabilities

- `chunk-entity-enrichment`: 文档级业务实体抽取（核心 3 类 company/report_period/sec_code 渲染进 prompt + 可选 person/currency/report_type 作为补充字段）、注入 chunk metadata、经 RAGContext 渲染进 prompt 的完整链路。

### Modified Capabilities

- `retrieval-quality`: 检索上下文的 prompt 渲染新增实体字段（`to_prompt_text` 渲染 entities），影响生产与 RAGAS NLI 共用的上下文格式。
- `evaluation-pipeline`: RAGAS NLI 上下文（`to_prompt_text`）随实体渲染变化，评估脚本 `generate_answers_and_contexts` 消费的上下文格式对齐。

## Impact

- **依赖**：`pymupdf==1.28.2`（升级）、`pymupdf4llm==1.28.2`（新增）
- **修改文件**：`src/parsers/pymupdf_parser.py`（pymupdf4llm 替代 + 标题树提取）、`src/services/document_service.py`（标题段定位反推 heading_path、实体抽取调用、meta_info 聚合）、`src/rag/context.py`（RAGContext.entities + to_prompt_text 渲染）、`src/rag/retrieval.py`（rerank 透传 entities）、`src/config/`（ENTITY_* 常量/开关/prompt）、`src/infra/llm/prompt_manager.py`（当前日期）、`src/infra/search/query_router.py` + `src/services/agent_service.py`（clarify 动态化）
- **新增文件**：实体抽取器（规则层 + LLM 兜底层）、标题段区间定位器
- **数据**：存量文档清除重建（ChromaDB + MySQL document 表）
- **测试**：新增实体抽取、标题段定位单测；更新 `tests/agents/graph/test_graph.py` 等 RAGContext 构造处
