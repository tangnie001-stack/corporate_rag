# observability-logging Specification

## Purpose
TBD - created by archiving change logging-convention-migration. Update Purpose after archive.
## Requirements
### Requirement: 分层事件前缀

系统日志 SHALL 使用分层前缀标识事件归属。前缀主表 SHALL 含 `[retrieval]` / `[verify]` / `[agent]` / `[session]` / `[db]` / `[llm]`（处理层）与 `[cli]`（离线工具）/ `[app]`（入口与全局兜底）。前缀表 SHALL 为开放登记制：新语义面前缀经 `log_events.py` 注册表 + 规范主表登记后启用，不预建未使用前缀。同一类事件全系统 SHALL 只有一个统一前缀与措辞，不得散落多种写法；`retrieval_signal:` 为 P1 既有信号保留前缀（已知例外，见「检索行为信号日志」Requirement），不并入 `[retrieval]`。

#### Scenario: 检索事件统一前缀

- **WHEN** 任何模块记录检索相关事件（发起/完成/失败/空结果）
- **THEN** 日志行 SHALL 以 `[retrieval]` 开头并带 k=v 字段（如 `[retrieval] search start query=... kb_id=...`），可用一条 grep 聚合全部检索里程碑事件

#### Scenario: 节点事件统一前缀

- **WHEN** agent 循环节点（verify/agent/format）记录自身事件
- **THEN** 日志行 SHALL 以对应节点名作为前缀段（如 `[verify] completeness missing=[2023]`）

#### Scenario: 开放登记制

- **WHEN** 一个尚未有前缀归属的语义域（如入库/解析/分块）首次记录事件
- **THEN** 该域在注册表登记新前缀并同步规范主表后启用，不得在调用点内联新前缀绕过登记

### Requirement: message 英文 k=v

业务/技术事件日志的 message 文本 SHALL 使用英文小写 k=v 格式（`key=value` 空格分隔），不使用中文自由文本；中文 SHALL 仅用于展示给最终用户的文案（集中在 `SSEInteractionTexts`）。

#### Scenario: 中文仅限用户可见文案

- **WHEN** 记录一次内部事件（如检索、会话锁、DB 调用）
- **THEN** message 为英文 k=v（如 `[session] lock acquired session_id=...`），不出现中文叙述

### Requirement: 五级级别语义

系统日志 SHALL 按五级语义使用：`debug`（诊断细节，默认关闭）、`info`（正常流程里程碑）、`warning`（降级/可恢复异常，禁止用于"正常但少见"）、`error`（单点失败已处理）、`exception`（透传型异常，带完整 traceback）。与 rules.md 异常分类联动。

#### Scenario: warning 不滥用

- **WHEN** 某路径是正常但少见的执行分支
- **THEN** 使用 `info`（或 `debug`）而非 `warning` 记录，避免 warning 泛滥淹没真问题

### Requirement: 检索行为信号日志

系统 SHALL 提供 `retrieval_signal:` 前缀的结构化信号日志，用于在线诊断"哪条 query 检索质量差"。信号类型：`reretrieve`（同 turn 二次检索）/ `to_web`（转联网）/ `abstain_after_retrieve`（检索后拒答）/ `unsupported`（judge 无支撑）/ `cited`（正常引用，对照基线）/ `empty_result`（检索空）。行格式 SHALL 含 `signal` / `query` / `iteration` / `kb_id` 字段；trace_id SHALL 由日志框架自动注入（不写入 message）；信号埋点 SHALL 经统一 helper 收口（query 完整记录、JSON 转义保持行可解析、格式一致，附加字段与 `log_event` 同走值类型编码）。`retrieval_signal:` 为 P1 Change 2 既有契约的**保留前缀**（已 sync 主干 spec），与 `[retrieval]` 前缀并存：检索域聚合需 `[retrieval]` + `retrieval_signal:` 两条 grep 模式，归已知例外。

#### Scenario: 定位检索差的问题 query

- **WHEN** 一条 query 在线链路触发 `to_web` 信号（绑定 KB 却走 search_web）
- **THEN** 日志出现 `retrieval_signal: signal=to_web query="..." iteration=N kb_id=... reason=...`，携带自动注入的 trace_id，可按 trace 回放该请求的检索全过程

#### Scenario: 信号格式统一

- **WHEN** 任意埋点记录行为信号
- **THEN** 均通过统一 helper 输出同构行（前缀 + k=v），不因埋点位置不同而格式漂移

#### Scenario: 信号附加字段不破坏行结构

- **WHEN** 信号行附加字段（如 `reason`）含空格或特殊字符
- **THEN** helper 按值类型编码处理该字段（引号 + JSON 转义），行保持可解析

### Requirement: query 完整记录（取消截断）

结构化日志行（`retrieval_signal` 与 `retrieve replay` 事件）中的 query/搜索词 SHALL 完整记录、不按固定长度截断；为保证 k=v 行可机器解析，query SHALL 以双引号包裹并按 JSON 转义（值内 `"` `\` 与换行须转义），不得破坏行的字段切分。

#### Scenario: 长 query 完整保留

- **WHEN** 检索/联网使用的 query 超过 40 字符
- **THEN** 结构化行中的 query 为原文完整值（JSON 转义后置于引号内），可无歧义用于离线重放

#### Scenario: 含特殊字符不破坏行结构

- **WHEN** query 含双引号/反斜杠/换行
- **THEN** 日志行经转义后仍为单行、字段可切分，不因特殊字符产生歧义

### Requirement: 事件注册表驱动与 helper 边界

全事件 SHALL 集中在 `src/core/log_events.py` 的 `EventSpec` 注册表登记（`name / prefix / level / fields`），前缀与信号白名单 SHALL 以注册表为单一事实源。事件 key SHALL 为 `Event` 枚举成员（值 = 事件名），`log_event` SHALL 按 spec 渲染 prefix/事件词并按 spec.level 路由日志级别（调用点不传前缀与级别）。info/warning/error 三类事件 SHALL 经注册表驱动 helper 输出；仅 exception 日志 SHALL 保留 `logger.xxx` 直调（traceback + raise 语义），message 文本遵循前缀 + 英文 k=v 规范。事件定义错误（枚举成员与注册表不一致）SHALL 在 import 期 fail-fast；拼错事件名（引用不存在成员）在 import 期以 AttributeError 拦截，helper 无运行期查表失败路径。注册表不承诺静态类型拦截事件字段拼写——字段键错误由迁移批 review 与抽查兜底。

#### Scenario: 未登记事件名被拦截

- **WHEN** 记录一个尚未登记的事件（或拼错已有事件名）
- **THEN** 该事件无法以枚举成员被引用（import 期 AttributeError）或枚举与注册表不一致在模块加载期即抛，日志不以静默自由文本落盘

#### Scenario: 降级日志保持级别语义

- **WHEN** 记录一次 fallback 降级或重试（warning 语义事件）
- **THEN** 该事件以 spec 登记的 level=warning 经 helper 输出，不被降级成 info，也不散落为手写直调文本

### Requirement: 会话上下文注入

session_id SHALL 在请求生命周期内经日志框架统一注入日志行固定段（与 trace_id 同构：`current_session_id` ContextVar → 请求入口设置 → patcher 写入 extra → `_LOG_FORMAT` 加段），**每一行**（含 helper 事件与 logger 直调行）SHALL 可关联会话；无会话场景（CLI/后台任务/收编行）SHALL 留空段。埋点处 SHALL NOT 手工拼写 `session_id=`（由框架注入，防漏防不一致）。

#### Scenario: 按会话聚合全量日志

- **WHEN** 需要查看某个会话在时间轴上的全部请求行为
- **THEN** 以该 session_id 过滤日志文件，helper 行与降级/异常直调行均被命中，无需依赖 trace_id 逐条钻取

### Requirement: 检索重放上下文日志

系统每次 `retrieve_kb` 执行 SHALL 落一条 `[retrieval] retrieve replay` 事件行，作为该 trace 的检索重放机器输入；行字段 SHALL 含 `query`（完整 + JSON 转义）/ `query_len` / `kb_id` / `iteration` / `top_k` / `dedup_max_per_doc` / `hybrid` / `rerank`；trace_id 由日志框架自动注入。

#### Scenario: 按 trace 离线重放一次检索

- **WHEN** 拿到一条 trace_id，怀疑检索环节有问题
- **THEN** 从该 trace 的 `retrieve replay` 事件行可直接取出 query 全文、kb_id 与当时的 top_k/去重 N 值，无需回查其它来源即可对当前 KB 重放该次检索，并把重放参数与"当时值"对照

### Requirement: 离线重放 CLI

系统 SHALL 提供重放命令（`python -m src.cli.replay_trace --trace <id>`）：读取该 trace 的日志（扫全部 `app_*.log`，**段位无关解析**：按 trace 子串过滤、取首个 ` - ` 之后为 message，兼容 `_LOG_FORMAT` 演进前后的新旧文件混存）→ 解析其 `retrieve replay` 事件（按 iteration 顺序）与行为信号 → 对当前 KB、当前配置重放检索并打印 top 片段（含来源与分数），并把事件行的当时参数并排对照、差异标注（drift 检测）。SHALL NOT 提供检索参数覆盖项（`--max-per-doc` 等）：检索栈内部读模块常量不可覆盖，去重 N 的 A/B 属离线实验。trace 无检索轮时 SHALL 给出提示。输出 SHALL 标注"对当前 KB 重放（非历史快照）"。

#### Scenario: 一条命令定位检索问题

- **WHEN** 对某 trace 运行重放命令
- **THEN** 输出按 iteration 还原每次检索的命中片段，可据此分诊（召回漏 / 排序错 / 同文档上下文不足），无需手工抄录 query 与 kb_id

#### Scenario: 参数漂移对照

- **WHEN** 事件记录的检索参数（top_k/dedup/hybrid/rerank）与当前配置不同
- **THEN** CLI 重放输出并排显示"当时值 vs 本次值"并标注差异，提示检索行为变化可能来自配置漂移而非 query 本身

### Requirement: 事件命名与值类型编码

系统日志事件名 SHALL 为英文小写、空格分隔单词（≤ 2~3 词，如 `search done` / `regen stop`），键名 SHALL 用 snake_case；类名/函数名、裸自由句 SHALL 不作为事件名。值编码 SHALL 由统一 helper 实现并作为其唯一责任：int/bool 裸写；字符串按 token 安全字符集（仅含 `[A-Za-z0-9_./:@-]` 且无空格）判定，token 安全则裸写，其余（含空格/中文/引号/反斜杠/控制符）SHALL 双引号包裹并按 JSON 转义；数组/容器 SHALL 用紧凑 JSON 文本（如 `missing=[2023,2025]`）；时长 SHALL 记整数毫秒（`latency_ms=8428`）。query/搜索词 SHALL 完整记录、不按固定长度截断。session_id SHALL 由日志框架按行注入（见「会话上下文注入」Requirement），埋点不手工拼写。

#### Scenario: 含数组与中文的字段可解析

- **WHEN** 记录含缺失年份数组与中文 query 的事件
- **THEN** 输出形如 `[verify] check done kb_id=.. missing=[2023,2025] answer_len=..`，数组与 query 均不破坏行的字段切分

#### Scenario: 事件命名统一

- **WHEN** 各层迁移批重写旧自由文本日志
- **THEN** 事件名符合空格词规范（`verify_node` 类写法迁移为 `[verify] check done`），类名/函数名不再作为事件名

### Requirement: 生成层可观测（LLM 摘要事件，不进原文）

系统 SHALL 记录生成层摘要事件以支撑 L2 诊断，本期覆盖**主 agent 推理 + verify judge 边界**：主 agent 每轮推理（agent 主模型调用点）记 `[agent] model turn`（model / usage_in / usage_out / fallback / latency_ms / session_id / iteration）；verify 执行忠实度 judge 前 SHALL 记 `[verify] judge start`，结束后记 `[verify] judge done`（含 unsupported_count，judge 的 usage 本期不追）。judge / temporal / query_router 等其余 LLM 调用本期 SHALL NOT 添加摘要（演进路径：抽统一 LLM 调用层后由 `[llm] model call` 覆盖）。LLM 原始输出 SHALL NOT 进入默认日志；完整答案与引用 SHALL 经 SSE 回放（`/api/sessions/events`）与 MySQL 会话表获取，需要原始 prompt/输出时经 `LLM_LOG_CONTENT` 开关临时开启。

#### Scenario: 态 A 不跑 judge 可正面断言

- **WHEN** 未绑定 KB 纯对话经过 verify
- **THEN** 该 trace 日志**不含** `[verify] judge start`（judge start 行成为"跑过 judge"的正面锚，便于 e2e 断言态 A 未执行 judge）

#### Scenario: 按 trace 还原生成摘要

- **WHEN** 排查一条回答质量问题时按 trace 检索
- **THEN** 可得到该请求的模型、usage、fallback、judge 结论等摘要，而答案原文经 SSE/会话表回放获取
