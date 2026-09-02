# logging-convention-migration Tasks

## 1. 规范文档（本次核心交付）

- [ ] 1.1 起草 `docs/agents/rules.md`「日志约定」完整章节：分层前缀体系（方案 A）、message 英文 k=v、五级级别语义、与异常分类联动
- [ ] 1.2 `CLAUDE.md` 增加日志简则（指向 rules.md 归属文档）
- [ ] 1.3 同步 `docs/agents/glossary.md`（如有新术语：retrieval_signal / 分层前缀）与文档组织表登记
- [ ] 1.4 全量盘点 239 处 logger 调用 → 生成存量迁移清单（按模块分组、标注目标前缀），作为 3.x 分批依据

## 2. 统一 helper

- [ ] 2.1 在 `src/core/` 提供日志 helper：`log_event(prefix, event, **fields)`（或 `retrieval_signal(signal, query, iteration, **fields)`），统一 query 截断 40、k=v 拼装
- [ ] 2.2 `retrieval_signal` 埋点 helper 单测（格式稳定、query 截断、trace_id 自动注入不重复）

## 3. 存量迁移（分批，文档先行定稿后逐批执行）

- [ ] 3.1 试点批：`[retrieval]` 类（rag_tools / web_tools / retrieval.py 的检索/联网事件）迁移到新前缀 + retrieval_signal 埋点（含 Change 2 的 5 个行为信号点）
- [ ] 3.2 `[verify]` 类：verify_node 全部日志迁移
- [ ] 3.3 `[agent]` 类：agent_node / workflow 日志迁移
- [ ] 3.4 `[session]` / `[db]` / `[llm]` 类：chat / infra 层日志迁移
- [ ] 3.5 cli / 入口中文日志 → 英文 k=v 迁移（保留用户可见文案不动）
- [ ] 3.6 每批回归：`pytest tests/ -v` 全过；`grep` 抽查确认同层事件可统一聚合

## 4. 验证

- [ ] 4.1 ruff / pyright 无新增 error
- [ ] 4.2 日志样例人工抽查：同层事件一条 grep 可全捞；无中英混行（除用户可见文案）
- [ ] 4.3 检索行为信号端到端验证：跑一条真实 query，日志能定位到 reretrieve/to_web/cited 信号（配合 P1 Change 2）
