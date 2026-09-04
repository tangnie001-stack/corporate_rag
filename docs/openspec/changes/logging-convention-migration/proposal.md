# logging-convention-migration Proposal

## Why

全系统 239 处 logger 调用缺乏统一的 message 格式规范，导致可观测性差、检索质量诊断（P1 retrieval-quality 在线行为信号）无从下手。实测混乱三类：

1. **语言混用**：中文业务日志（cli/知识图谱/入口约 19 处）与英文技术日志并存，`grep 测试集` 和 `grep testset` 各捞一半。
2. **事件"身份"写法三种并存**：类名前缀（`KBRouter:` / `format_node:`）、裸动词（`verify skipped` / `agent iteration=`）、k=v 流（`tool=` / `judge:`）。同一事件没有统一起始锚点，无法整体聚合。
3. **同类事件措辞分散**：检索动作有 `RAG search starting` / `search failed` / `rerank failed` / `tool=retrieve_kb` / `judge:` 至少 5 种开头；级别语义与 rules.md 脱节（warning 81/239 泛滥、debug 6/239 几乎不用）。

## What Changes

### 1. 统一日志格式规范（本次核心交付，文档先行）

- 新建 `docs/agents/logging-rules.md` 作为日志格式**唯一归属文档**（`docs/agents/rules.md`「日志约定」整体迁入，原处只留一行指针；`CLAUDE.md`「文档组织」表登记），内容：
  - **行模板 = 分层前缀 + 事件 + k=v**：`[retrieval] search done kb_id=.. result_count=..`；同一事件全系统只有一个名字
  - **前缀主表（开放登记制）**：`[retrieval]`/`[verify]`/`[agent]`/`[session]`/`[db]`/`[llm]`（6 处理层）+ `[cli]`（离线工具）+ `[app]`（入口/全局兜底）；新语义面前缀经注册表 + 主表登记后启用，不预建；`retrieval_signal:` 为 P1 既有信号保留前缀（检索聚合需两条 grep 模式），归已知例外
  - **事件命名**：英文小写、空格分隔单词（≤2~3 词，如 `search done` / `regen stop`）；键 snake_case；禁用类名/函数名当事件、工具名作事件词
  - **message = 英文小写 k=v**；中文仅限用户可见文案（`SSEInteractionTexts`）
  - **值格式 = 需要才引（方案 C）+ 类型编码**：int/bool 裸写；token 安全字符串（精确字符集见 logging-rules.md）裸写；其余字符串双引号 + JSON 转义；数组用紧凑 JSON 文本；时长整数毫秒；query/搜索词完整记录不截断；`retrieval_signal` 附加字段与 `log_event` 同走 helper 编码
  - **级别语义**：debug（诊断默认关）/ info（里程碑）/ warning（降级可恢复，禁"正常但少见"）/ error（单点已处理）/ exception（透传带 traceback）
- `CLAUDE.md` 保留日志简则指针（指向 logging-rules.md）
- **会话上下文注入**：新增 `current_session_id` ContextVar（`trace_context.py`），请求入口设置；日志行固定段携带 session_id（与 trace_id 同构，`_LOG_FORMAT` 加段）——能取到会话的请求全行可关联，无会话（CLI/后台）为空段
- **事件定义层（单一事实源）**：新增 `src/core/log_events.py`——Signal 枚举 / ReplayEvent dataclass / 全事件 `EventSpec` 注册表（name/prefix/level/fields）；`logging.py` 现存的 `_LOG_PREFIXES`/`_RETRIEVAL_SIGNALS` 白名单收敛至此；**核心先登记**（信号×6 + replay + [retrieval] 里程碑），3.2~3.5 每批迁移时把该批事件增量登记（不做全量 227 处一次性建类）
- **helper 注册表驱动（按级别收口）**：info/warning/error 事件经 helper 按事件 key 查 spec 渲染（prefix/事件词/级别取注册表，调用点不传级别，helper 按 spec.level 路由日志级别）；**仅 exception 保留 `logger.xxx` 直调**（traceback 语义，文本同样规范化）；事件 key 为**枚举**（值 = 事件名），拼错 import 期即拦截，枚举与注册表一致性由 import 校验兜底（无运行期查表失败路径）；字段键拼错靠迁移批 review 抽查（不承诺 pyright 静态拦截）

### 2. 检索行为信号日志（P1 Change 2 的诊断地基，纳入本规范）

- 统一前缀 `retrieval_signal:`，信号类型：`reretrieve` / `to_web` / `abstain_after_retrieve` / `unsupported` / `cited` / `empty_result`
- 行格式：`retrieval_signal: signal={} query="{}" iteration={} kb_id={} result_count={} ...`
- trace_id 由 logging patcher 自动注入第三段（不写入 message）
- 埋点 helper 收口在 `src/core/`（query 完整记录并以 JSON 转义保持行可解析、格式统一），不在各埋点重复写

### 3. trace 自包含重放（L1 检索 + L2 生成，一条 trace 可诊断）

- **取消 query 一律截 40**：query/搜索词在结构化日志行完整记录（配合双引号 + JSON 转义），保证 trace 内数据足以无歧义重放
- **L1 检索重放**：每次 `retrieve_kb` 执行落一条 `[retrieval] retrieve replay` 上下文事件行（query 全文 / query_len / kb_id / iteration / top_k / dedup_max_per_doc / hybrid / rerank）——query 全文与 kb_id 是重放输入；top_k/dedup 等参数是"当时值"，供 CLI 重放后并排对照做 drift 检测
- **L2 生成层可观测（摘要，不进全文）**：主 agent 每轮推理落 `[agent] model turn`（model / usage_in / usage_out / fallback / latency_ms / session_id / iteration，埋 agent_node 主模型调用点）；verify judge 靠 `[verify] judge start|done`（done 带 unsupported_count）边界包住。judge / temporal / query_router 等其余 LLM 调用本期不加摘要（演进：抽统一 LLM 调用层时补 `[llm] model call` 一次性覆盖）。**LLM 原文不进默认日志**（走 SSE `/api/sessions/events` 回放 + MySQL 会话表答案 + `LLM_LOG_CONTENT` 深日志开关）
- 提供 `replay_trace` CLI：输入 trace_id → 扫全部 `app_*.log`（段位无关解析，兼容新旧格式）→ 提取 replay 事件与行为信号 → 对当前 KB、当前配置重放检索打印 top 片段，并把事件行的"当时参数"并排对照、差异标注（drift 检测）。本期实现 **L1**；L2 数据源读取（SSE/会话表）作为后续增强扩展点，本期只在设计写明路径。检索栈内部读模块常量、参数不可覆盖，故 **无 `--max-per-doc`**——N=1 vs N>1 的去重 A/B 属离线实验（改 settings 重启跑整链路），不在 replay CLI 范围

### 4. 存量日志迁移（分批，3.1 试点已迁，3.2~3.5 在本 change 顺序执行）

- 迁移清单见 `migration-inventory.md`；批次顺序：3.2 `[verify]` → 3.3 `[agent]` → 3.4 `[session]`/`[db]`/`[llm]` → 3.5 cli/入口；每批独立 commit + 回归，并把该批事件登记进 `EventSpec` 注册表

## Capabilities

### New Capabilities
- `observability-logging`: 统一日志事件前缀（分层）、message 格式（英文 k=v）、级别语义（debug/info/warning/error/exception 五级）与检索行为信号（`retrieval_signal:`）的日志规范与 helper

### Modified Capabilities
- （无 — 不改既有 spec 级别行为，只改日志文本/格式）

## Impact

- `docs/agents/logging-rules.md` — 新建（格式唯一归属文档：前缀主表 + 开放登记制 + 事件命名 + 值类型编码/token 字符集 + 级别语义 + 已知例外；事件全集以 `log_events.py` 注册表为准、文档不手抄）
- `docs/agents/rules.md` — 「日志约定」迁出后留一行指针；`CLAUDE.md`「文档组织」表登记新行
- `src/infra/llm/trace_context.py` — 新增 `current_session_id` ContextVar；请求入口设置
- `src/core/log_events.py` — 新增（EventSpec 注册表 + Signal 枚举 + ReplayEvent；收敛 `_LOG_PREFIXES`/`_RETRIEVAL_SIGNALS` 白名单）
- `src/core/logging.py` — helper 注册表驱动升级（值类型编码、query 完整、`log_retrieve_replay`）；`_LOG_FORMAT` 加 session_id 上下文段
- 存量 227 处 logger 调用 — 分批迁移（3.1 试点已迁；3.2~3.5 顺序执行，逐批登记 EventSpec）
- 测试 — helper/编码单测（含 round-trip 解析回读）；各迁移批回归
