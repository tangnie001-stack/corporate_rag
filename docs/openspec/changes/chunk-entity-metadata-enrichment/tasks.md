## 1. 依赖升级

- [ ] 1.1 pyproject.toml 更新 `pymupdf==1.28.2`（原 1.27.2.3）、新增 `pymupdf4llm==1.28.2`
- [ ] 1.2 安装依赖并验证 `pymupdf4llm.to_markdown()` 对 `data/test_docs/neusoft_2025_q1.pdf` / `tencent_2024_annual.pdf` / `canki_2019_annual.pdf` 的输出（标题层级、表格格式）
- [ ] 1.3 实测 pymupdf4llm 表格 Markdown 与 `table_preserving.TABLE_PATTERN` 的兼容性，不兼容则调整 TABLE_PATTERN

## 2. 解析层改造（pymupdf4llm 替代 + 标题树提取）

- [ ] 2.1 `src/parsers/pymupdf_parser.py`：用 `pymupdf4llm.to_markdown(doc, page_chunks=True, show_progress=False, write_images=False)` 替代手写 fitz 文本/表格提取（保留扫描件检测逻辑，适配 MIN_TEXT_CHARS 判定）
- [ ] 2.2 各页 `text` 拼接为 `full_text`，同时构建页码区间表 `[(page_num, start_offset, end_offset)]`，供现有 `_enrich_chunk_pages` 反推页码复用
- [ ] 2.3 从 Markdown 输出提取标题树 `[(level, heading), ...]`（按 `#` 前缀数），写入 `ParseResult.heading_tree`（新增字段），供规则层和标题段定位使用
- [ ] 2.5 `src/parsers/docx_parser.py`：用 python-docx 的 heading 样式（`paragraph.style.name` 判断 Heading 级别或中文样式名）提取标题树，写入 `ParseResult.heading_tree`；txt 无标题，`heading_tree` 留空
- [ ] 2.6 实体抽取按文件类型分流：PDF/docx 走完整三层（文件名+标题栈+LLM），txt 走文件名+LLM 兜底（无标题栈规则层）
- [ ] 2.4 解析回归：neusoft/tencent/canki 三份样本的 ParseResult 结构兼容（total_pages/总字符/扫描件标记/page 字段），跑 `pytest tests/parsers/`

## 3. 标题段定位（方案 C：只写 metadata 不改 content）

- [ ] 3.1 新增标题段区间定位器：由标题树 + full_text 建立 `[(heading_path, start_offset, end_offset), ...]` 区间表（docx/txt 无标题树时区间表为空，跳过 heading_path 绑定）
- [ ] 3.2 `src/services/document_service.py`：分块后按 `full_text.find(chunk.content)` 反推 heading_path，写入 `chunk.metadata["heading_path"]`（与 `_enrich_chunk_pages` 页码反推合并成一次遍历）
- [ ] 3.3 确认 `inject_heading_prefix` 不修改 content（heading_path 只在 metadata，分块时传空）
- [ ] 3.4 单测：标题段定位正确性（chunk 归属正确标题段、表格 chunk content 不变）

## 4. 实体抽取三层链路

- [ ] 4.1 `src/config/const.py`：新增 `ENTITY_TYPES`（核心 3 类：company/report_period/sec_code）、`ENTITY_RENDER_ORDER`（核心 3 类渲染顺序）；`src/config/settings.py`：新增 `ENTITY_LLM_FALLBACK`（三态：`off` / `on` / `auto`，默认 `auto`）、`ENTITY_TEXT_PREFIX_LEN`（默认 600）
- [ ] 4.2 `src/config/prompts.py`：新增 `ENTITY_EXTRACTION_SYSTEM_PROMPT` / `ENTITY_EXTRACTION_USER_TEMPLATE`（约束"规则结果与原文一致时保留，仅当原文无依据或明显不是实体时才纠正"；可选实体 person/currency/report_type 仅顺带返回，不专门触发）
- [ ] 4.3 新增实体抽取器规则层：文件名正则 + 标题栈规则层（复用 financial_rag ContextStack 的 6 条 HEADING_EXTRACTORS + sec_code 正则，父级继承、兄弟零泄漏；可选实体照常提取但标记非核心）
- [ ] 4.4 新增实体抽取器 LLM 兜底层：三态开关 `ENTITY_LLM_FALLBACK`（off=纯规则；on=每文档无条件走 LLM；auto=规则结果为空或缺关键类型才走 LLM），输入 = 文件名 + 全量标题树 + 正文前 N 字符，调用 CLASSIFY_MODEL，输出 `{rule_correct, reason, entities}`，失败降级规则层结果
- [ ] 4.5 `src/services/document_service.py`：process_document 中调用抽取器（文档级一次），结果注入每个 chunk.metadata + 合并 `document.meta_info` 的 `{"entities": {...}}`
- [ ] 4.6 单测：三层抽取（文件名命中/标题栈命中/LLM 纠正误配/三态开关行为：off 不调 LLM、on 每文档调、auto 规则空或缺关键类型才调）

## 5. 检索消费（RAGContext 透传 + 渲染）

- [ ] 5.1 `src/rag/context.py`：RAGContext 新增 `entities: dict = field(default_factory=dict)`；`to_prompt_text()` 按 `ENTITY_RENDER_ORDER` 渲染存在的实体
- [ ] 5.2 `src/rag/retrieval.py`：`rerank_results` 从 chunk.metadata 透传实体字段进 `RAGContext.entities`
- [ ] 5.3 更新 `tests/agents/graph/test_graph.py` / `test_state.py` 的 RAGContext 构造（entities 默认空，无需改动则跳过）
- [ ] 5.4 单测：to_prompt_text 有实体/无实体两种渲染格式

## 6. 当前日期注入

- [ ] 6.1 `src/infra/llm/prompt_manager.py`：`get_system_prompt()` 追加今日日期（"今天是 YYYY年M月D日"），Langfuse prompt 也在 `_get()` 后统一追加
- [ ] 6.2 单测：get_system_prompt 返回含当前日期

## 7. clarify 动态化

- [ ] 7.1 `src/config/prompts.py`：`CLASSIFIER_USER_TEMPLATE` 增加 `{kb_entities}` 占位符
- [ ] 7.2 `src/infra/search/query_router.py`：从 document.meta_info 聚合 KB 候选实体，注入 classifier prompt
- [ ] 7.3 `src/services/agent_service.py`：`SUGGESTIONS_MAP` 硬编码替换为动态 KB 候选（保留 default 兜底）
- [ ] 7.4 单测：clarify 分支用 KB 候选而非硬编码

## 8. 存量重建

- [ ] 8.1 脚本/命令：清除现有 ChromaDB collection + document 表记录（按 KB 清理或全清）
- [ ] 8.2 重新入库评估 KB（b9e74e820e0a4bad8472304446e54f5c 的 2 份文档），验证实体 metadata 落库

## 9. 回归验证

- [ ] 9.1 `pytest tests/ -v` 全部通过、`ruff check .` 无错误、`pyright src/` 不新增 error
- [ ] 9.2 跑 RAGAS 评估（同一测试集），对比 faithfulness/answer_relevancy/context_precision/context_recall 与基线（faithfulness 0.9333）
- [ ] 9.3 更新 `docs/api_contract.md`（如响应结构变化）与 `src/cli/README.md`（评估工作流）
