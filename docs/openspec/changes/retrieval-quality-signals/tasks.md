# retrieval-quality-signals Tasks

> 依赖顺序：本 change 排在 agent-loop-hardening（C1-2 verify 决策化）之后实施——to_web 埋点与 C1-2 同处 verify/search_web 交界；logging-convention-migration 先行或同步（helper）。

## 1. 行为信号埋点（依赖 logging-convention-migration 的 retrieval_signal helper）

- [x] 1.1 `RequestContext` 加 `web_guided: bool = False`（verify 指派联网标记）
- [x] 1.2 埋点助手接入：rag_tools.retrieve_kb → reretrieve（同 turn 第 2+ 次调用，读 ctx 记调用序列）+ empty_result（kb_id 非空且结果 0）
- [x] 1.3 verify 侧：**注入联网指引 SystemMessage 时置 `ctx.web_guided=True`**（verify_node 302-312 处）；unsupported（judge _unsupported 非空）
- [x] 1.4 web_tools.search_web：to_web 仅检索降级路径激活——读 `ctx.web_guided` 为 True 时不产 to_web（② 属数据覆盖不足非检索缺陷）
- [x] 1.5 agent_service：abstain_after_retrieve（检索过且 _is_abstention）
- [x] 1.6 format_node：cited 对照基线（带 kind）
- [x] 1.7 **态限定实现**：所有缺陷信号仅 `kb_id` 非空时 emit（态 A 不产），helper 封装态判断或埋点处判断
- [x] 1.8 单测：态 A 调 search_web 不产 to_web / 空检索不产 empty_result；态 B 各信号激活；verify 指派联网（web_guided=True）不产 to_web；cited 两态都产

## 2. dedup 参数化 + A/B 实验

- [x] 2.1 `settings.py` 加 `RETRIEVAL_MAX_PER_DOC`（默认 1）；`retrieval.py` `_dedup_by_doc_id` 支持每文档 N 条
- [x] 2.2 单测：默认 N=1 行为不变；N=2 保留每文档前 2 条
- [ ] 2.3 构造"真实差 query 小测试集"（从行为信号标记的 query 收集，≥10 条）—— **待真实 KB + 行为信号日志后验证**（无真实业务 KB，信号日志为回放，未见真实差 query 清单）
- [ ] 2.4 A/B 脚本：N=1 vs N=2/3 跑 RAGAS context_recall/precision/faithfulness，输出对照—— **待真实 KB + 行为信号日志后验证**（脚本 `src/cli/compare_dedup.py` 已合入，未对真实测试集跑通出对照）
- [ ] 2.5 依据 A/B 结论定 N（或维持 1），结论写入 change 记录—— **待真实 KB + 行为信号日志后验证**（结论未产出）

## 3. 离线下钻补全（detail 可定位）

- [ ] 3.1 `eval_ragas` 输出 detail_json 增补"每条 query 检索明细"（dense/bm25/dedup 命中/rerank 顺序），供低分下钻—— **待真实 KB + 行为信号日志后验证**（代码已合入且单测断言结构，但"低分可定位"验收需真实 query 跑通下钻，本任务未运行）
- [ ] 3.2 验证：对一条标记 query 跑 RAGAS → detail_json 能看出是哪一环问题（dense 无命中 / dedup 截断 / rerank 排错）—— **待真实 KB + 行为信号日志后验证**（需 2.3 的标记 query 后执行）

## 4. 质量门禁

- [x] 4.1 `pytest tests/ -v` 全量通过；ruff / pyright 无新增 error
- [ ] 4.2 端到端：跑一条绑 KB query，日志出现对应信号 + trace_id 可回放 —— **待真实 KB + 行为信号日志后验证**（无真实业务 KB、live 日志无 retrieval_signal，未运行）
- [x] 4.3 契约同步：api_contract.md / glossary.md（如新增 RETRIEVAL_MAX_PER_DOC 配置说明）

---

### 勾选说明（Task 6 收尾质量门禁）
- 勾选项均有实现代码 / 自动化测试支撑；item → 证据映射见 `.superpowers/sdd/task-6-report.md`。
- 未勾项 2.3-2.5 / 3.1-3.2 / 4.2 均依赖**真实业务 KB + 行为信号日志**（当前环境无真实 KB、live 日志无 retrieval_signal，信号均来自单测回放），保持未勾并标注**待真实 KB + 行为信号日志后验证**；人工执行步骤见 `.superpowers/sdd/task-6-report.md`「待人工验证步骤」。
