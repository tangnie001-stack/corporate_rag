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
- [ ] 3.2 `[verify]` 类：verify/ 包日志迁移到 `[verify]` 前缀 + 事件命名（check done / skip / guide / judge start|done 等）；**judge start** 为 e2e"态A 不跑 judge"正面锚；把 verify 事件登记进 EventSpec 注册表
- [ ] 3.3 `[agent]` 类：agent_node / workflow 迁移（iteration done / iteration limit / **model turn**——L2 摘要事件，埋 agent_node 主模型调用点：model / usage_in / usage_out / fallback / latency_ms / session_id / iteration），登记 EventSpec
- [ ] 3.4 `[session]` / `[db]` / `[llm]` 类：chat / infra 层日志迁移，登记 EventSpec（session 事件带 session_id）
- [ ] 3.5 cli / 入口中文日志 → 英文 k=v 迁移：cli 工具归 `[cli]`，main.py 入口生命周期与全局异常兜底归 `[app]`（去 `├─` 制表符；保留用户可见文案不动），登记 EventSpec
- [ ] 3.6 每批回归：`pytest tests/ -v` 全过；`grep` 抽查确认同层事件可统一聚合

> 注记：3.1 试点已完成；**3.2-3.5 在本 change 内顺序执行**（按 migration-inventory.md 清单），每批独立 commit + 回归，并把该批 info/warning/error 事件（spec 登记级别）增量登记进 `src/core/log_events.py` 的 Event 枚举 + EventSpec 注册表，exception 直调规范化不建 spec；3.6 随各批次回归时勾选

## 4. 验证

- [x] 4.1 ruff / pyright 无新增 error
- [x] 4.2 日志样例人工抽查：同层事件一条 grep 可全捞；无中英混行（除用户可见文案）
- [ ] 4.3 检索行为信号端到端验证：跑一条真实 query，日志能定位到 reretrieve/to_web/cited 信号（配合 P1 Change 2）——待联调：依赖 retrieval-quality-signals（P1 Change 2）落地后，重启 app 加载新码后人工跑真实 query 验证 → 承接: e2e-playwright-regression（信号冒烟，`retrieval_signal:` 锚点本 change 3.1 已交付）

## 5. 统一格式治理 + 事件注册表 + trace 自包含重放 + 会话注入（增补）

- [ ] 5.1 规范归属迁移：新建 `docs/agents/logging-rules.md`（自 `rules.md`「日志约定」整体迁入：行模板 / **前缀主表（6 层 + [cli] + [app]，开放登记制）** / 事件命名 / **值类型编码 + token 安全字符集** / 级别语义 / **已知例外（`retrieval_signal` 保留前缀，检索聚合需两条 grep）** / **事件全集以 `log_events.py` 注册表为准、文档不手抄**）；`rules.md` 留一行指针；`CLAUDE.md`「文档组织」表登记；`glossary.md` 术语同步
- [ ] 5.2 `src/core/log_events.py`：新建 EventSpec 注册表 + **Event 枚举（值 = 事件名）** + Signal 枚举 + ReplayEvent dataclass；**import 期一致性校验（每个 Event 成员有对应 spec、每个 spec name ∈ Event）**；**收敛 `logging.py` 的 `_LOG_PREFIXES`/`_RETRIEVAL_SIGNALS` 白名单**；登记保留前缀 `retrieval_signal`；核心先登记（信号×6 / replay / [retrieval] 里程碑）
- [ ] 5.3 `src/core/logging.py` helper 注册表驱动升级：`log_event` 改按 `Event` 枚举成员调用并按 **spec.level 路由日志级别（info/warning/error，调用点不传前缀与级别；仅 exception 保留 `logger.xxx` 直调）**；值类型编码（token 安全字符集 `[A-Za-z0-9_./:@-]`：int/bool 裸写 / token 裸写 / 其余引号 + JSON 转义 / 数组紧凑 JSON / 时长整数毫秒）；`retrieval_signal` 去截断（query 完整 + JSON 转义）且**附加字段同走编码**；新增 `log_retrieve_replay(...)`；`tests/core/test_logging_helpers.py` 更新并含 **round-trip 用例（编码输出可被 replay 解析器切回原字段）**；事件定义错误由 import 校验拦截（无运行期查表失败路径）
- [ ] 5.4 存量 `[retrieval]` 清理与调用点适配：rag_tools / retrieval.py / tavily_client 等改完整 query + 按 Event 枚举调 helper；3.1 残留的 warning 直调（rerank timeout / rerank failed 等）映射为 spec level=warning 的注册事件走 helper
- [ ] 5.5 `retrieve_kb` 执行落 `[retrieval] retrieve replay` 事件行（字段齐全，helper 收口）
- [ ] 5.6 新增 `src/cli/replay_trace.py`（**本期 L1**）：`--trace <id>` **扫全部 `app_*.log`（按天轮转，trace 可跨天；段位无关解析：trace 子串过滤 + 取最后一个 ` - ` 之后为 message，兼容新旧格式混存）** 解析该 trace 的 replay 事件（按 iteration）与行为信号 → 对当前 KB、当前配置重放检索打印 top 片段（source/page/score/片段头），**事件行"当时参数"并排对照、差异标注（drift）**；`--max-per-doc N` 仅覆盖去重参数；输出标注"对当前 KB 重放（非历史快照）"；无检索轮 trace 给提示；单测覆盖日志解析与重放（检索 mock，不发真实网络），**解析器与 5.3 helper 编码 round-trip 配套**
- [ ] 5.7 session_id 会话注入：`trace_context.py` 新增 `current_session_id` ContextVar；请求入口（stream_chat 起点）设置；`_LOG_FORMAT` 加 session_id 段；logging patcher 写 extra；单测覆盖（请求内行行带 session、CLI 空段）
- [ ] 5.8 回归：`pytest tests/ -v` 全过；ruff / pyright 无新增 error；`openspec validate` 通过

> 注记：
> - 5.x 取消 query 截断是对 2.x 已交付 helper 语义的修订；helper 注册表驱动（5.3）后 3.1 已迁调用点需改按 Event 枚举调用（约 10 处，输出行不变）。
> - helper 按级别收口 info/warning/error（spec.level 路由），**仅 exception 直调规范化、不建 spec**（design D8）。
> - `[verify] judge start|done`（3.2）与 `[agent] model turn`（3.3，埋主 agent 推理点）为 L2 生成层摘要事件，随迁移批落地；judge/temporal/query_router 其余 LLM 调用不加摘要（演进见 design D9）。
> - replay 事件行属 `[retrieval]` 层新事件，与试点批（3.1）同格式体系；CLI 依赖 embedding/Chroma 环境，经 `docker compose exec app python -m src.cli.replay_trace ...` 运行。
> - L2 数据源读取（SSE `/api/sessions/events` 回放 + MySQL 会话表答案）为本期设计说明、后续增强实现（design D7）。
> - session_id 注入（5.7）打破原 Non-Goal"不改 `_LOG_FORMAT`"，仅加一段、与 trace_id 同构（design D10）。
