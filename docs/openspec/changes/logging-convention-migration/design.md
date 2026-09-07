# logging-convention-migration Design

> **SUPERSEDED（2026-09-07）**：本文档中 verify judge 相关日志设计（`[verify] judge start/done`）
> 已随在线忠实度 judge 移除作废；其余日志分层/事件规范不受影响。

## Context

全系统 239 处 logger 调用无统一 message 格式规范，实测三类混乱：
1. **中英混用**：cli/知识图谱/入口约 19 处中文叙述 + 绝大多数英文技术日志并存
2. **事件"身份"三种写法**：类名前缀（`KBRouter:` `format_node:`）/ 裸动词（`verify skipped` `agent iteration=`）/ k=v 流（`tool=` `judge:`）
3. **同类事件措辞分散**：检索动作 ≥5 种开头（RAG search / search failed / rerank failed / tool=retrieve_kb / judge:）；级别语义与 rules.md 脱节（warning 81/239、debug 6/239）

现有地基（保留不动）：
- `src/core/logging.py` `_LOG_FORMAT`：`时间 | level | trace_id | name:function:line - message`，trace_id 由 patcher 自动注入第三段
- rules.md「日志约定」只有级别选择（warning vs exception），无 message 格式规范
- 好苗头：`tool=` / `judge:` k=v 流已局部存在，需文档化收口

## Goals / Non-Goals

**Goals:**
- 立统一规范（分层前缀 + 英文 k=v + 五级语义）进 rules.md + CLAUDE.md 简则
- 定义检索行为信号日志（`retrieval_signal:`，P1 Change 2 诊断地基）
- 提供统一日志 helper，query 完整记录（不截断）与 k=v 拼装收口
- 存量迁移机制（分批清单），**文档先行、存量后迁**

**Non-Goals:**
- 本次不批量改存量 239 处（分批任务 3.x 后续逐模块执行）
- 不改 trace_id 注入机制 / 日志文件轮转；`_LOG_FORMAT` 仅按 D10 增加 session_id 上下文段
- 不动用户可见文案（SSEInteractionTexts 里的中文保持）
- 不引入日志采集/告警系统（filebeat/监控属运维向）
- 不迁移"未排期"域（document_service/parsers/chunking 等，前缀登记制留待各域排期）

## Decisions

### D1: 事件前缀 = 分层前缀（方案 A）

```
[retrieval] ...  检索层（rag_tools / web_tools / retrieval.py）
[verify] ...     验证节点（verify 包）
[agent] ...      agent 主循环（agent_node / workflow）
[session] ...    会话管理（chat manager / persistence）
[db] ...         DB 层（repo）
[llm] ...        LLM 调用层
[cli] ...        离线工具（cli/ 评估/重建/检查，无请求上下文）
[app] ...        应用边界（main.py 生命周期 + 全局异常兜底）
```

**开放登记制**：前缀表是开放集合，事件归哪个语义面就打哪层前缀；新语义面前缀在 `src/core/log_events.py` 注册表 + logging-rules.md 主表登记后启用，不预建未使用前缀。未排期域（document_service/parsers/chunking/解析/分块等）到各自排期批再按事件语义登记，本 change 不锁。

**核心原则：同一类事件全系统只有一个统一前缀 + 措辞**，可用一条 grep 聚合。弃类名前缀（`KBRouter:`）——它把"类"当事件身份，同类事件跨类就散了。

### D2: message = 英文小写 k=v

```
✅ logger.info("[retrieval] search start query={} kb_id={}", query, kb_id)
❌ logger.info("RAG search starting hybrid: kb_id={}", kb_id)
❌ logger.info("加载测试集: {} 条 QA 对", n)
```

中文仅限用户可见文案（`SSEInteractionTexts`）。事件消息是给 grep + 开发者看的，机器要能解析。

### D3: 五级级别语义（扩 rules.md 现有「日志约定」）

| 级别 | 语义 | 示例 |
|---|---|---|
| `debug` | 诊断细节，默认关闭 | 检索中间态、token 流 |
| `info` | 正常流程里程碑 | 请求进出、agent 迭代、检索/联网完成 |
| `warning` | 降级/可恢复异常 | fallback 生效、重试、超时 |
| `error` | 单点失败已处理 | LLM 失败降级 |
| `exception` | 透传型异常（带 traceback + raise） | 透传型（rules.md 已有定义） |

**关键纠正**：warning 泛滥（81 处）源于拿它记"正常但少见"——warning 只留给"需要人注意的可恢复降级"。

### D4: 检索行为信号日志（纳入统一体系，P1 Change 2 依赖）

```
前缀: retrieval_signal:
类型: reretrieve / to_web / abstain_after_retrieve / unsupported / cited / empty_result
格式: retrieval_signal: signal={} query="{}" iteration={} kb_id={} result_count={} ...
trace_id: logging patcher 自动注入第三段，不写入 message
```

helper 收口 `src/core/log_signal.py`（或并入 logging.py），query 完整记录（不截断）、以双引号包裹并 JSON 转义（值含空格/引号/换行时行仍可解析）、k=v 拼装统一。**query 加引号 + 转义**便于按字段解析（与 `tool=` 裸值现状的权衡：信号行是新格式，从第一天就结构化）。附加字段与 `log_event` 同走 helper 值类型编码。

**已知例外（保留前缀）**：`retrieval_signal:` 是 P1 Change 2 既有交付且已 sync 主干 spec（retrieval-quality），不并入 `[retrieval]`。`retrieval_signal` 作为保留前缀登记进注册表；logging-rules.md 写明"检索域聚合需 `[retrieval]` + `retrieval_signal:` 两条 grep 模式"，归已知例外，待将来前缀体系重构时统一。

### D5: 存量迁移分批机制

- 盘点 239 处 → 生成迁移清单（按模块分组标注目标前缀）
- 分批执行（tasks 3.1~3.5），每批独立 commit + `pytest` 回归
- 每批抽查：同层事件一条 grep 可全捞；无中英混行（除用户可见文案）

### D6: query 完整记录（取消一律截 40）

查询文本是检索重放的最小复现输入——截断（原 40 字符）会让长 query 无法精确重放，故**取消对 query 的截断约定**。为保持 k=v 行可机器解析，query 以双引号包裹并按 JSON 转义（`"` `\` 换行等）。**值编码是 helper 的唯一责任**：埋点传原生值，helper 按值类型自动决定裸写或引号 + JSON 转义（token 安全字符集见 D8），调用点不需要也不应手工加引号（消除"过渡期由调用点自引号"的说法）。

**Trade-off：日志行可能变长**；但 trace 自包含换来"一条命令离线重放"。query 全文属内部日志（`/data/logs`），与会话表 MySQL 记录同一敏感级，不新增越权面。

### D7: trace 自包含检索重放 + replay_trace CLI（L1 检索层）

- 每次 `retrieve_kb` 执行落一条 `[retrieval] retrieve replay` 事件行（info，helper 收口），字段：`query`（全文 + JSON 转义）/ `query_len` / `kb_id` / `iteration` / `top_k` / `dedup_max_per_doc`（`settings.RETRIEVAL_MAX_PER_DOC`）/ `hybrid` / `rerank`。其中 **query 全文与 kb_id 是重放输入**；top_k/dedup/hybrid/rerank 是"当时值"，供重放后并排对照做 drift 检测（重放本身用当前配置，不喂回检索栈——search/rerank 内部读模块常量，事件参数无法原样覆盖）。行为信号、决策等其它事件行格式不变。
- `src/cli/replay_trace.py`：`--trace <id>` 读日志目录（按天轮转，**扫全部 `app_*.log`**，trace 跨天可完整取到）→ 解析该 trace 的全部 `retrieve replay` 事件（按 iteration 顺序）与行为信号 → 对每条事件**用当前 KB、当前配置重放检索**（检索词 = 事件内 agent 实际使用的 query，非原始用户输入）打印 top 片段 `(source/page/score/片段头)`，并**把事件行的当时参数并排对照、差异标注**（drift 检测：`row: dedup=2 → 本次: dedup=1`）。**无 `--max-per-doc`**：去重 N=1 vs N>1 A/B 需改 search 签名或用去重前结果，属离线实验（改 settings 重启跑整链路 / 独立 change 给 search 加可选参数），不在 replay CLI 范围。trace 无检索轮（纯联网/纯对话）时提示"该 trace 无检索重放事件"。**解析段位无关**：按 trace_id 子串过滤行、取首个 ` - ` 之后为 message，不依赖 `|` 分段——兼容 `_LOG_FORMAT` 加 session 段前后的新旧日志混存。
- 输出标注"对当前 KB 重放（非历史快照）"；检索依赖 embedding/rerank 模型与 Chroma 环境，经容器运行：`docker compose exec app python -m src.cli.replay_trace --trace trace_xxx`。
- **L2（生成层）为后续增强**：本期只做 L1；L2 数据源路径 = SSE `/api/sessions/events` 回放 + MySQL 会话表（答案/引用）+ `LLM_LOG_CONTENT` 深日志，设计写清即可，不在本期实现。

### D8: 事件定义层 = EventSpec 注册表 + Event 枚举 + 注册表驱动 helper（按级别收口）

格式规范文档定 `docs/agents/logging-rules.md`（rules.md「日志约定」迁入、留指针；CLAUDE 文档表登记）。代码侧新增 `src/core/log_events.py`，并把 `logging.py` 现存的 `_LOG_PREFIXES`/`_RETRIEVAL_SIGNALS` 手维护白名单收敛至此（单一事实源，避免三处漂移）：

- `Signal(str, Enum)`：6 种行为信号名固定（`retrieval_signal` 保留前缀行用）
- `Event(str, Enum)`：普通事件枚举，值 = 事件名——`log_event` 的事件 key
- `ReplayEvent` dataclass(frozen)：replay 9 字段定死，`to_kv()` 统一编码
- `EventSpec` dataclass(frozen)：`name / prefix / level / fields`——描述普通事件，注册表 key = 事件名
- **import 期一致性校验**：模块加载时断言"每个 `Event` 成员有对应 `EventSpec`、每个 spec 的 name ∈ Event"——事件定义错误在加载阶段即抛
- 注册顺序：核心先登记（信号×6 / replay / [retrieval] 里程碑），随后 **3.2~3.5 每批迁移把该批事件（含 warning/error 事件）增量登记**；不为未迁移的 227 处预建 spec（YAGNI）

**helper 注册表驱动（按级别收口）**：info/warning/error 事件经 `log_event(Event.RERANK_TIMEOUT, **fields)` 调用，helper 按 spec 取 prefix/事件词并**按 spec.level 路由日志级别**（`logger.log(spec.level, message)`）——调用点不传前缀与级别。**收口边界**：
- helper 服务 info/warning/error 三类事件（级别由 spec 登记，调用方不感知）；**仅 exception 保留 `logger.xxx` 直调**（需 traceback + raise 语义），message 文本同样按规范（前缀 + 英文 k=v）规范化，exception 行不建 EventSpec（携带 `e` 自由文本，不适合 k=v 规格化）
- **事件 key = `Event` 枚举**：拼错事件名 = 引用不存在的成员 → **import 期 AttributeError**，运行期不可达；未登记事件无法被引用（枚举无成员），登记遗漏由 import 一致性校验兜底——helper 无运行期查表失败路径
- **不承诺 pyright 静态拦截字段拼写**：字段键错 → `**fields` 动态收参静态拦不住，靠迁移批 review + 3.6 grep 抽查兜底
- 事件统一带 `session_id`（D10 全行注入，helper 不需手拼）、`iteration`（agent 循环上下文，能取到处）

**值类型编码**（helper 内唯一实现，`需要才引`）：int/bool 裸写；字符串按 **token 安全字符集**判断——仅含 `[A-Za-z0-9_./:@-]` 且无空格的 token 裸写，其余（含空格/中文/引号/反斜杠/控制符）→ 双引号包裹 + JSON 转义；数组/容器 → 紧凑 JSON 文本（无空格，`[] ,` 入裸 token 集）；时长整数毫秒（`latency_ms=8428`）；query/搜索词完整记录不截断。`retrieval_signal` 附加字段与 `log_event` 同走此编码（消除现状裸 `str()` 拼接破坏行的隐患）。

### D9: L2 生成层可观测与 LLM 全文策略

"为什么答错"靠**摘要事件**而非 LLM 原文定位，本期覆盖**主 agent 推理 + verify judge 边界**：
- 主 agent 每轮推理落 `[agent] model turn`：model / usage_in / usage_out / fallback / latency_ms / session_id / iteration，埋点在 agent_node 主模型调用点（`model.astream` 前后，model/usage/latency/fallback 均可得）
- verify judge 靠 `[verify] judge start`（跑 judge 即打，为 e2e"态A 不跑 judge"提供正面锚）+ `[verify] judge done unsupported_count=..` 边界包住；judge 的 usage 本期不追
- judge / temporal / query_router 等**其它 LLM 调用本期不加摘要**——演进路径：抽统一 LLM 调用层时在其封装补一层 `[llm] model call` 自动覆盖全部调用点
- **LLM 原文不进默认日志**：完整答案与工具链走 SSE `/api/sessions/events` 回放 + MySQL 会话表；需要原始 prompt/输出时临时开 `LLM_LOG_CONTENT`
- `usage` 字段名对齐 `AgentState._token_usage` / capture 现成结构，落代码时确认

### D10: session_id 上下文注入（固定格式段）

session_id 在单次请求内不变，故与 trace_id 同构做成独立上下文变量，而非逐层透传：
- `trace_context.py` 新增 `current_session_id: ContextVar[str]`（默认空），请求入口（建立 RequestContext 的 stream_chat 起点）设置
- `_LOG_FORMAT` 增加 session_id 段（`{extra[session_id]:36}`），logging patcher 读 ContextVar 写入 extra——**每一行**（含 helper 事件与 logger 直调行）统一携带会话；无会话场景（CLI/后台任务/收编的 uvicorn 行）为空段，与 trace_id 现状一致
- 打破原 Non-Goal"不改 `_LOG_FORMAT`"：改动仅为加一段，注入机制与日志轮转不变；部署前后新旧格式日志同目录混存，replay CLI 段位无关解析天然兼容（D7）

## 两条路线（态 A / 态 B）对照

日志规范本身**两态通用**（都是这套前缀/k=v/级别体系），两态差异只在**具体事件内容**：

| 事件 | 态 A（未选 KB，纯对话） | 态 B（选 KB，RAG） |
|---|---|---|
| `[retrieval]` | 少（retrieve_kb 空返回，可能无） | 多（真检索各环节） |
| `retrieval_signal:` | **不产**缺陷信号（见 Change 2 D1 态限定） | 产全部 6 种信号 |
| `[verify]` | 态 A 管道日志（web_citation_guard） | 态 B 管道日志（completeness/护栏/judge） |
| `[agent]` | 主循环日志（无 retrieve 轮） | 主循环日志（含 retrieve 轮） |

分层前缀体系让两态在同一前缀下自然共存——grep `[verify]` 能看到两态各自管道在跑，再靠 k=v（kb_id 有/无）区分。

## Risks / Trade-offs

- [文档先行 + 存量后迁期间新旧格式并存] → 分批迁移 + 每批抽查收敛；新代码写日志前先查规范
- [query 完整记录致结构化行变长] → 只在重放/信号等机器读取行放开全文（JSON 转义保持可解析）；普通字段随迁移批次收敛，不铺开
- [离线重放依赖当前 KB（非历史快照）] → CLI 输出明示语义，定位目标是"同样 query 在当前配置为何仍差/已好"
- [query 加引号与既有 `tool=` 裸值不一致] → 信号行是新格式第一天就结构化；存量迁移时经 helper 编码统一，调用点不再手拼
- [中文日志迁移遗漏（cli 工具非核心链路）] → 盘点清单覆盖全部 src；cli 属低优先但列入 3.5
- [helper 与 logging patcher 职责边界] → helper 只做 message 拼装，不重复注入 trace_id/session_id（patcher 已做）
- [信号行保留前缀（`retrieval_signal:`）与"一条 grep 聚合"原则不一致] → 归已知例外显式写入 logging-rules.md + 注册表保留前缀登记，待前缀体系重构时统一
- [`_LOG_FORMAT` 加 session_id 段（破原 Non-Goal）] → 仅加一段、与 trace_id 同构；无会话行空段与现状一致
- [warning/error 事件级别由 spec 登记、helper 路由] → 调用点不传级别，级别语义审查发生在登记时；warning 泛滥整治靠 3.2~3.5 逐批把误用改 info
- [exception 行不经 helper（直调保留 traceback）] → exception 文本按规范（前缀 + 英文 k=v）规范化、不建 EventSpec，review 抽查
- [未排期域前缀未锁] → 开放登记制，各域排期批迁移时登记，本 change 不预建
