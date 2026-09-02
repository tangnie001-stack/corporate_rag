# 存量日志迁移清单

生成日期：2026-09-03（盘点基线：239 处 logger 调用）
目标规范：docs/agents/rules.md「日志约定」（分层前缀 / 英文 k=v / 五级语义）

> 盘点命令（输出见文末）：
> `grep -rn "logger\.\(debug\|info\|warning\|error\|exception\)(" src/ --include=*.py | grep -v __pycache__`
>
> - 归属批次对应 change tasks 3.1~3.5；3.1 三文件由本 change 自己的 Task 3 迁移，其余 3.2~3.5 为后续批次。任务未覆盖的文件归"未排期"，作为后续独立批次候选。
> - 目标前缀为迁移建议：前缀表（rules.md「日志约定」前缀表）覆盖的文件按其归属标前缀；cli 无前缀表条目归 `[cli]`；入口/API 边界、auth/eval/文档域、解析/分块等前缀表未覆盖的标记 `—`，迁移前需先扩展前缀表定义。

## 文件清单

| 文件 | logger 数 | 目标前缀 | 含中文日志? | 归属批次 |
|------|----------|---------|------------|---------|
| src/agents/tools/rag_tools.py | 3 | [retrieval] | 否 | 3.1（已迁） |
| src/agents/tools/web_tools.py | 5 | [retrieval] | 否 | 3.1（已迁） |
| src/rag/retrieval.py | 8 | [retrieval] | 否 | 3.1（已迁） |
| src/agents/graph/verify_node.py | 6 | [verify] | 否 | 3.2 |
| src/agents/graph/agent_node.py | 2 | [agent] | 否 | 3.3 |
| src/agents/graph/workflow.py | 1 | [agent] | 否 | 3.3 |
| src/chat/manager.py | 5 | [session] | 否 | 3.4 |
| src/chat/persistence.py | 4 | [session] | 否 | 3.4 |
| src/infra/db/file_store.py | 4 | [db] | 否 | 3.4 |
| src/infra/db/vector_store/search.py | 12 | [db] | **是（1 行）** | 3.4 |
| src/infra/db/vector_store/store.py | 5 | [db] | 否 | 3.4 |
| src/infra/db/vector_store/embedding.py | 2 | [db] | 否 | 3.4 |
| src/infra/db/vector_store/client.py | 2 | [db] | 否 | 3.4 |
| src/cli/eval_ragas.py | 20 | [cli] | **是（1 行）** | 3.5 |
| src/cli/eval_ragas_generate.py | 18 | [cli] | **是（9 行）** | 3.5 |
| src/cli/rebuild_bm25.py | 6 | [cli] | 否 | 3.5 |
| src/cli/compare_rewrite.py | 4 | [cli] | 否 | 3.5 |
| src/cli/check_retrieval.py | 4 | [cli] | 否 | 3.5 |
| src/services/agent_service.py | 2 | [agent]/[session] 混合 | 否 | 3.3/3.4 |
| src/main.py | 12 | —（入口启动/异常兜底） | **是（8 行）** | 3.5（入口中文日志） |
| src/services/document_service.py | 17 | —（入库链路横跨解析/分块/向量/实体抽取） | 否 | 未排期 |
| src/rag/kb_router.py | 9 | [retrieval] | 否 | 未排期 |
| src/infra/llm/langfuse_tracing.py | 9 | [llm] | 否 | 未排期 |
| src/api/chat.py | 6 | [session] | 否 | 未排期 |
| src/infra/search/query_router.py | 6 | [retrieval] | 否 | 未排期 |
| src/parsers/pdf_heading_extractor.py | 6 | —（解析域） | 否 | 未排期 |
| src/rag/stream.py | 5 | [llm] | 否 | 未排期 |
| src/services/app_service.py | 5 | —（KB 生命周期存储/索引混合） | 否 | 未排期 |
| src/infra/llm/prompt_manager.py | 5 | [llm] | 否 | 未排期 |
| src/api/ragas_generate.py | 5 | —（eval 域） | 否 | 未排期 |
| src/agents/tools/ask_tools.py | 4 | [agent] | 否 | 未排期 |
| src/middleware/response_processor.py | 3 | —（API 边界，已带 `[API]` 类前缀） | 否 | 未排期 |
| src/core/logging.py | 3 | [db]（SQL echo 基础设施） | 否 | 未排期 |
| src/api/llm_test.py | 3 | [llm] | 否 | 未排期 |
| src/agents/graph/nodes.py | 3 | [agent] | 否 | 未排期 |
| src/services/auth_service.py | 2 | —（auth 域） | 否 | 未排期 |
| src/infra/search/tavily_client.py | 2 | [retrieval] | 否 | 未排期 |
| src/infra/search/document_entity_extractor.py | 2 | —（入库实体抽取） | 否 | 未排期 |
| src/infra/llm/llm_content_logging.py | 2 | [llm] | 否 | 未排期 |
| src/chunking/strategies/table_preserving.py | 2 | —（分块域，已带 `[table_preserving]` 类前缀） | 否 | 未排期 |
| src/api/sessions.py | 2 | [session] | 否 | 未排期 |
| src/api/kb_eval.py | 2 | —（eval 域） | 否 | 未排期 |
| src/api/documents.py | 2 | —（文档域） | 否 | 未排期 |
| src/models.py | 2 | [llm] | 否 | 未排期 |
| src/parsers/txt_parser.py | 1 | —（解析域） | 否 | 未排期 |
| src/infra/search/bm25_index.py | 1 | [db]（索引维护） | 否 | 未排期 |
| src/chunking/strategies/qa.py | 1 | —（分块域，已带 `[qa]` 类前缀） | 否 | 未排期 |
| src/chunking/strategies/parent_child.py | 1 | —（分块域，已带 `[parent_child]` 类前缀） | 否 | 未排期 |
| src/chat/streaming.py | 1 | [session] | 否 | 未排期 |
| src/api/feedback.py | 1 | —（feedback 域） | 否 | 未排期 |
| src/api/auth.py | 1 | —（auth 域） | 否 | 未排期 |
| **合计** | **239** | | **4 文件 / 19 行** | |

> agent_service.py（编排层横切多域）按行归类：`AgentService initialized with compiled graph`（L507）→ [agent]；`clarify item convert failed`（L349，会话追问项回放转换）→ [session]。

## 中文日志清单（3.5 重点 + 3.4 夹带）

共 19 行中文日志，分布在 4 个文件：

### src/cli/eval_ragas.py（1 行）

| 行号 | 中文日志原文 | 迁移后英文 k=v |
|------|-------------|---------------|
| 465 | `"加载测试集: {} 条 QA 对"` | `[cli] testset loaded count={}` |

### src/cli/eval_ragas_generate.py（9 行）

| 行号 | 中文日志原文 | 迁移后英文 k=v |
|------|-------------|---------------|
| 131 | `"校对问题失败，保留原文: {} \| {}"` | `[cli] question proofread failed fallback=original question="{}" err={}` |
| 262 | `"查询知识库元信息失败: {}"` | `[cli] kb meta fetch failed err={}` |
| 287 | `"ChromaDB 中未找到文档的 chunk: {}"` | `[cli] chunks not found doc_id={}` |
| 306 | `"白名单中所有文档在 ChromaDB 中均无 chunk 数据"` | `[cli] no chunk data whitelist_docs=all` |
| 388 | `"发现已保存的知识图谱: {}"` | `[cli] knowledge graph found file={}` |
| 393 | `"构建知识图谱 ({} 个 chunk)..."` | `[cli] knowledge graph build start chunks={}` |
| 419 | `"知识图谱已保存: {}"` | `[cli] knowledge graph saved file={}` |
| 423 | `"开始生成测试集 ({} 条)..."` | `[cli] testset generation start size={}` |
| 429 | `"TestsetGenerator 调用失败"` | `[cli] testset generator call failed` |

### src/infra/db/vector_store/search.py（1 行，随 3.4 [db] 批迁移）

| 行号 | 中文日志原文 | 迁移后英文 k=v |
|------|-------------|---------------|
| 103 | `"搜索 collection '{}' 失败: {}"` | `[db] search collection failed kb_id={} err={}` |

### src/main.py（8 行，入口日志，前缀待定）

| 行号 | 中文日志原文 | 迁移后英文 k=v |
|------|-------------|---------------|
| 43 | `"财务问答 API 正在启动"` | `app starting` |
| 47 | `"财务问答 API 正在关闭"` | `app stopping` |
| 111 | `"基础设施异常: {} {}"` | `infra error code={} message={}` |
| 113 | `"业务异常: {} {}"` | `biz error code={} message={}` |
| 126 | `"HTTP 异常: {} {}"` | `http error status={} detail={}` |
| 140 | `"参数校验异常: {}"` | `validation error errors={}` |
| 155 | `"未处理的系统异常: {} {}"` | `unhandled exception method={} url={}` |
| 164 | `"  ├─ 嵌套第{}层: type={} msg={}"` | `exception chain depth={} type={} msg={}` |

> main.py 为入口/异常兜底，前缀表未覆盖；建议值只转英文 k=v，分层前缀待入口边界域定义后补（L164 的 `├─` 制表符随迁移一并去除）。

## 盘点命令实际输出

```
$ grep -rn "logger\.\(debug\|info\|warning\|error\|exception\)(" src/ --include=*.py | grep -v __pycache__ | wc -l
239

$ ... | sed 's|\(.*\):[0-9]*:.*|\1|' | sort | uniq -c | sort -rn
（51 个文件按 logger 数降序，见上表各文件行）
```
