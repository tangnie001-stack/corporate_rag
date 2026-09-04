# 存量日志迁移清单

生成日期：2026-09-03（盘点基线更新：227 处 logger 调用；上一版 239 处见 git 历史）
目标规范：`docs/agents/logging-rules.md`（分层前缀主表 6 层 + `[cli]` + `[app]` / 英文 k=v / 五级语义 / 值类型编码 + query 完整记录；事件全集以 `src/core/log_events.py` 注册表为准）

> 盘点命令：
> `grep -rn "logger\.\(debug\|info\|warning\|error\|exception\)(" src/ --include=*.py | grep -v __pycache__`

> 说明：
> - 归属批次对应 change tasks 3.1~3.5；3.1 三文件已迁（余留 logger.warning/exception 直调），3.2~3.5 为后续批次。任务未覆盖的文件归「未排期」，作为后续独立批次候选。
> - 计数仅为 `logger.xxx` 直调；经 `log_event`/`retrieval_signal` helper 的事件行属新格式，不在迁移范围。
> - P1 后结构变化：`verify_node.py` → `verify/` 包（计入 3.2）；`kb_router.py` 已删（其日志随死代码清理移除）。

## 文件清单

| 文件 | logger 数 | 目标前缀 | 含中文日志? | 归属批次 | 说明 |
|------|----------|---------|------------|---------|------|
| src/cli/eval_ragas.py | 20 | [cli] | 是（1 行） | 3.5 | 离线评估（含 1 行中文） |
| src/cli/eval_ragas_generate.py | 18 | [cli] | 是（9 行） | 3.5 | 测试集生成（含 9 行中文） |
| src/services/document_service.py | 17 | — | 否 | 未排期 | 入库链路横跨解析/分块/向量/实体 |
| src/infra/db/vector_store/search.py | 12 | [db] | 是（1 行） | 3.4 | 向量检索（含 1 行中文降级日志） |
| src/main.py | 12 | [app] | 是（8 行） | 3.5（入口中文日志） | 入口生命周期 + 全局异常兜底（8 行中文，归 [app]，去 ├─ 制表符） |
| src/infra/llm/langfuse_tracing.py | 9 | [llm] | 否 | 未排期 | Langfuse 埋点 |
| src/core/logging.py | 7 | [db] | 否 | 未排期 | 日志基建自身（SQL echo/helper 自检，helper 为 log_event/retrieval_signal 内 warning） |
| src/api/chat.py | 6 | [session] | 否 | 未排期 | 聊天 SSE 端点 |
| src/cli/rebuild_bm25.py | 6 | [cli] | 否 | 3.5 | BM25 重建 |
| src/infra/search/query_router.py | 6 | [retrieval] | 否 | 未排期 | 查询改写/路由（词法/向量路径） |
| src/parsers/pdf_heading_extractor.py | 6 | — | 否 | 未排期 | 解析域 |
| src/api/ragas_generate.py | 5 | — | 否 | 未排期 | eval 域 |
| src/chat/manager.py | 5 | [session] | 否 | 3.4 | Redis 会话管理 |
| src/infra/db/vector_store/store.py | 5 | [db] | 否 | 3.4 | 向量存储 |
| src/infra/llm/prompt_manager.py | 5 | [llm] | 否 | 未排期 | 提示词组装 |
| src/rag/stream.py | 5 | [llm] | 否 | 未排期 | 检索流式包装 |
| src/services/app_service.py | 5 | — | 否 | 未排期 | KB 生命周期存储/索引混合 |
| src/agents/tools/ask_tools.py | 4 | [agent] | 否 | 未排期 | ask_user 澄清工具 |
| src/agents/tools/web_tools.py | 4 | [retrieval] | 否 | 3.1（已迁） | 联网工具（同上） |
| src/chat/persistence.py | 4 | [session] | 否 | 3.4 | MySQL 持久化 |
| src/cli/check_retrieval.py | 4 | [cli] | 否 | 3.5 | 检索检查 |
| src/cli/compare_rewrite.py | 4 | [cli] | 否 | 3.5 | 改写 A/B |
| src/infra/db/file_store.py | 4 | [db] | 否 | 3.4 | 文件存储 |
| src/agents/graph/nodes.py | 3 | [agent] | 否 | 未排期 | format/kb_router 相关节点日志（kb_router 节点已删） |
| src/agents/graph/verify/regen_decision.py | 3 | [verify] | 否 | 3.2 | 缺失联网决策 |
| src/api/llm_test.py | 3 | [llm] | 否 | 未排期 | LLM 冒烟端点 |
| src/middleware/response_processor.py | 3 | — | 否 | 未排期 | API 边界，已带 [API] 类前缀 |
| src/agents/graph/agent_node.py | 2 | [agent] | 否 | 3.3 | agent 主循环 |
| src/agents/graph/verify/guardrails.py | 2 | [verify] | 否 | 3.2 | 态A 引用引导 / 态B KB 护栏 |
| src/agents/graph/verify/node.py | 2 | [verify] | 否 | 3.2 | verify 主节点 |
| src/api/documents.py | 2 | — | 否 | 未排期 | 文档端点 |
| src/api/kb_eval.py | 2 | — | 否 | 未排期 | eval 域 |
| src/api/sessions.py | 2 | [session] | 否 | 未排期 | 会话端点 |
| src/chunking/strategies/table_preserving.py | 2 | — | 否 | 未排期 | 分块域，已带 [table_preserving] 类前缀 |
| src/infra/db/vector_store/client.py | 2 | [db] | 否 | 3.4 | Chroma 客户端 |
| src/infra/db/vector_store/embedding.py | 2 | [db] | 否 | 3.4 | embedding |
| src/infra/llm/llm_content_logging.py | 2 | [llm] | 否 | 未排期 | LLM 内容记录 |
| src/infra/search/document_entity_extractor.py | 2 | — | 否 | 未排期 | 入库实体抽取 |
| src/infra/search/tavily_client.py | 2 | [retrieval] | 否 | 未排期 | Tavily 客户端 |
| src/models.py | 2 | [llm] | 否 | 未排期 | 模型工厂 |
| src/services/agent_service.py | 2 | [agent]/[session] | 否 | 3.3/3.4 | 编排层按行归类 |
| src/services/auth_service.py | 2 | — | 否 | 未排期 | auth 域 |
| src/agents/graph/verify/ask_confirm.py | 1 | [verify] | 否 | 3.2 | 联网询问 |
| src/agents/graph/verify/faithfulness.py | 1 | [verify] | 否 | 3.2 | 忠实度 judge |
| src/agents/graph/workflow.py | 1 | [agent] | 是（1 行） | 3.3 | 图构建（含 1 行中文入口日志） |
| src/agents/tools/rag_tools.py | 1 | [retrieval] | 否 | 3.1（已迁） | 检索工具（log_event/retrieval_signal 走 helper，余 warning/exception 直调） |
| src/api/auth.py | 1 | — | 否 | 未排期 | auth 端点 |
| src/api/feedback.py | 1 | — | 否 | 未排期 | feedback 端点 |
| src/chat/streaming.py | 1 | [session] | 否 | 未排期 | 流式任务注册表 |
| src/chunking/strategies/parent_child.py | 1 | — | 否 | 未排期 | 分块域，已带 [parent_child] 类前缀 |
| src/chunking/strategies/qa.py | 1 | — | 否 | 未排期 | 分块域，已带 [qa] 类前缀 |
| src/infra/search/bm25_index.py | 1 | [db] | 否 | 未排期 | BM25 索引维护 |
| src/parsers/txt_parser.py | 1 | — | 否 | 未排期 | 解析域 |
| src/rag/retrieval.py | 1 | [retrieval] | 否 | 3.1（已迁） | 检索层 |
| **合计** | **227** | | **5 文件 / 20 行** | | |

> `agent_service.py`（编排层横切多域）按行归类：图初始化 → [agent]；会话任务/SSE 收尾 → [session]。
> `core/logging.py` 的 7 处为 helper 未知前缀/信号自检（`log_event unknown prefix`/`retrieval_signal unknown signal`），属基建内部日志。

## 中文日志清单（3.5 重点 + 3.4/3.3 夹带）

共 20 行中文日志，分布在 5 个文件（行号为 2026-09-03 实测快照，批执行时以实际为准）：

### src/agents/graph/workflow.py（1 行）

| 行号 | 中文日志原文 | 迁移后英文 k=v |
|------|-------------|---------------|
| 85 | `"LangGraph StateGraph compiled: agent 循环 → verify → format"` | `[agent] graph compiled agent_loop→verify→format` |

### src/cli/eval_ragas.py（1 行）

| 行号 | 中文日志原文 | 迁移后英文 k=v |
|------|-------------|---------------|
| 483 | `"加载测试集: {} 条 QA 对"` | `[cli] testset loaded count={}` |

### src/cli/eval_ragas_generate.py（9 行）

| 行号 | 中文日志原文 | 迁移后英文 k=v |
|------|-------------|---------------|
| 131 | `"校对问题失败，保留原文: {} | {}"` | `[cli] question proofread failed question="{}" err={}` |
| 262 | `"查询知识库元信息失败: {}"` | `[cli] kb meta fetch failed err={}` |
| 287 | `"ChromaDB 中未找到文档的 chunk: {}"` | `[cli] chunks not found doc_id={}` |
| 306 | `"白名单中所有文档在 ChromaDB 中均无 chunk 数据"` | `[cli] no chunk data whitelist_docs=all` |
| 388 | `"发现已保存的知识图谱: {}"` | `[cli] knowledge graph found file={}` |
| 393 | `"构建知识图谱 ({} 个 chunk)..."` | `[cli] knowledge graph build start chunks={}` |
| 419 | `"知识图谱已保存: {}"` | `[cli] knowledge graph saved file={}` |
| 423 | `"开始生成测试集 ({} 条)..."` | `[cli] testset generation start size={}` |
| 429 | `"TestsetGenerator 调用失败"` | `[cli] testset generator call failed` |

### src/infra/db/vector_store/search.py（1 行）

| 行号 | 中文日志原文 | 迁移后英文 k=v |
|------|-------------|---------------|
| 103 | `"搜索 collection '{}' 失败: {}"` | `[db] search collection failed kb_id={} err={}` |

### src/main.py（8 行）

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

> main.py 为入口生命周期 + 全局异常兜底，归 `[app]` 前缀（本批登记）；L164 的 `├─` 制表符随迁移去除。
> workflow.py:85 为图编译入口日志（中文），随 3.3 [agent] 批迁移。
