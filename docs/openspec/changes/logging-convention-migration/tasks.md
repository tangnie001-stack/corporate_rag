# logging-convention-migration Tasks

## 1. 规范文档（本次核心交付）

- [x] 1.1 起草 `docs/agents/rules.md`「日志约定」完整章节：分层前缀体系（方案 A）、message 英文 k=v、五级级别语义、与异常分类联动
- [x] 1.2 `CLAUDE.md` 增加日志简则（指向 rules.md 归属文档）
- [x] 1.3 同步 `docs/agents/glossary.md`（如有新术语：retrieval_signal / 分层前缀）与文档组织表登记
- [x] 1.4 全量盘点 239 处 logger 调用 → 生成存量迁移清单（按模块分组、标注目标前缀），作为 3.x 分批依据

## 2. 统一 helper

- [x] 2.1 在 `src/core/` 提供日志 helper：`log_event(prefix, event, **fields)` 与 `retrieval_signal(signal, query, iteration, **fields)`，统一 k=v 拼装（query 由 5.2 起完整记录 + JSON 转义，不再截断）
- [x] 2.2 `retrieval_signal` 埋点 helper 单测（格式稳定、query 截断→完整转义由 5.2 更新、trace_id 自动注入不重复）

## 3. 存量迁移（分批，文档先行定稿后逐批执行）

- [x] 3.1 试点批：`[retrieval]` 类（rag_tools / web_tools / retrieval.py 的检索/联网事件）迁移到新前缀 + retrieval_signal 埋点（含 Change 2 的 5 个行为信号点）
- [ ] 3.2 `[verify]` 类：verify_node 全部日志迁移
- [ ] 3.3 `[agent]` 类：agent_node / workflow 日志迁移
- [ ] 3.4 `[session]` / `[db]` / `[llm]` 类：chat / infra 层日志迁移
- [ ] 3.5 cli / 入口中文日志 → 英文 k=v 迁移（保留用户可见文案不动）
- [ ] 3.6 每批回归：`pytest tests/ -v` 全过；`grep` 抽查确认同层事件可统一聚合

> 注记（试点批收尾）：3.2-3.5 后续批次——verify/agent/session/db/cli 层存量迁移，按 migration-inventory.md 清单独立实施（不在本 plan 试点范围）；3.6 随各批次回归时勾选

## 4. 验证

- [x] 4.1 ruff / pyright 无新增 error
- [x] 4.2 日志样例人工抽查：同层事件一条 grep 可全捞；无中英混行（除用户可见文案）
- [ ] 4.3 检索行为信号端到端验证：跑一条真实 query，日志能定位到 reretrieve/to_web/cited 信号（配合 P1 Change 2）——待联调：依赖 retrieval-quality-signals（P1 Change 2）落地后，重启 app 加载新码后人工跑真实 query 验证 → 承接: e2e-playwright-regression（信号冒烟，`retrieval_signal:` 锚点本 change 3.1 已交付）

## 5. trace 自包含检索重放（增补：query 完整记录 + replay 事件行 + replay_trace CLI）

- [ ] 5.1 规范同步：`docs/agents/rules.md`「日志约定」删除"query 一律截 40"，改为"query 完整记录；结构化行（retrieval_signal / retrieve replay）query 双引号 + JSON 转义；普通 k=v 值含空格自行引号包裹"；`glossary.md` 同步术语
- [ ] 5.2 `src/core/logging.py`：`retrieval_signal` 去掉截断（query 完整 + JSON 转义）；新增 `log_retrieve_replay(...)` helper 输出 `[retrieval] retrieve replay` 事件行（query 全文转义 / query_len / kb_id / iteration / top_k / dedup_max_per_doc / hybrid / rerank）；`tests/core/test_logging_helpers.py` 截断用例改为"完整 + 转义 + replay 行格式"用例
- [ ] 5.3 存量 `[retrieval]` 埋点 query[:40] 清理：rag_tools / retrieval.py / tavily_client 等日志改完整 query
- [ ] 5.4 `retrieve_kb` 执行落 `[retrieval] retrieve replay` 事件行（字段齐全，helper 收口）
- [ ] 5.5 新增 `src/cli/replay_trace.py`：`--trace <id>` 解析该 trace 的 replay 事件（按 iteration）与行为信号 → 对当前 KB 离线重放检索打印 top 片段（source/page/score/片段头）；`--max-per-doc N` 对照去重参数；输出标注"对当前 KB 重放（非历史快照）"；单测覆盖日志解析与重放（检索 mock，不发真实网络）
- [ ] 5.6 回归：`pytest tests/ -v` 全过；ruff / pyright 无新增 error；`openspec validate` 通过

> 注记：5.x 取消 query 截断是对 2.x 已交付 helper 语义的修订（spec D6/D7）；replay 事件行属 `[retrieval]` 层新事件，与试点批（3.1）同格式体系；CLI 依赖 embedding/Chroma 环境，经 `docker compose exec app python -m src.cli.replay_trace ...` 运行。
