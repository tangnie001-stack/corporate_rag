# logging-convention-migration Proposal

## Why

全系统 239 处 logger 调用缺乏统一的 message 格式规范，导致可观测性差、检索质量诊断（P1 retrieval-quality 在线行为信号）无从下手。实测混乱三类：

1. **语言混用**：中文业务日志（cli/知识图谱/入口约 19 处）与英文技术日志并存，`grep 测试集` 和 `grep testset` 各捞一半。
2. **事件"身份"写法三种并存**：类名前缀（`KBRouter:` / `format_node:`）、裸动词（`verify skipped` / `agent iteration=`）、k=v 流（`tool=` / `judge:`）。同一事件没有统一起始锚点，无法整体聚合。
3. **同类事件措辞分散**：检索动作有 `RAG search starting` / `search failed` / `rerank failed` / `tool=retrieve_kb` / `judge:` 至少 5 种开头；级别语义与 rules.md 脱节（warning 81/239 泛滥、debug 6/239 几乎不用）。

## What Changes

### 1. 立规范文档（本次核心交付，文档先行）

- `docs/agents/rules.md`「日志约定」章节扩写为完整规范：
  - **事件前缀 = 分层前缀（方案 A）**：`[retrieval] ...` / `[verify] ...` / `[agent] ...`，同一事件全系统只有一个名字
  - **message = 英文小写 k=v**；中文仅限用户可见文案（已收 `SSEInteractionTexts`）
  - **级别语义补齐**：debug（诊断细节，默认关）/ info（正常里程碑）/ warning（降级可恢复，禁记"正常但少见"）/ error（单点已处理）/ exception（透传带 traceback）
- `CLAUDE.md` 加一段日志简则（指向 rules.md 归属文档）

### 2. 检索行为信号日志（P1 Change 2 的诊断地基，纳入本规范）

- 统一前缀 `retrieval_signal:`，信号类型：`reretrieve` / `to_web` / `abstain_after_retrieve` / `unsupported` / `cited` / `empty_result`
- 行格式：`retrieval_signal: signal={} query="{}" iteration={} kb_id={} result_count={} ...`
- trace_id 由 logging patcher 自动注入第三段（不写入 message）
- 埋点 helper 收口在 `src/core/`（query 截断 40、格式统一），不在各埋点重复写

### 3. 存量日志迁移（分批，本次只建机制不扫全量）

- 建迁移清单 + 分批任务；按模块分批改到新前缀体系，每批独立 commit + 回归

## Capabilities

### New Capabilities
- `observability-logging`: 统一日志事件前缀（分层）、message 格式（英文 k=v）、级别语义（debug/info/warning/error/exception 五级）与检索行为信号（`retrieval_signal:`）的日志规范与 helper

### Modified Capabilities
- （无 — 不改既有 spec 级别行为，只改日志文本/格式）

## Impact

- `docs/agents/rules.md` — 「日志约定」章节扩写（归属文档登记位已存在）
- `CLAUDE.md` — 新增日志简则段
- `src/core/logging.py` — 可能加日志 helper（或新增 `src/core/log_signal.py`）
- 存量 239 处 logger 调用 — 分批迁移（本次机制 + 首批试点，不一次全量）
- 测试 — 无行为断言依赖日志文本（需确认）；如 logger 被 mock 的测试不受影响
