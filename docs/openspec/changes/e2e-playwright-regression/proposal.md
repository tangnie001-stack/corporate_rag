# e2e-playwright-regression Proposal

## Why

前端（deploy/nginx/html 静态页）与后端联调的问题只能靠真实浏览器交互暴露，现有 pytest 覆盖不到。当前用 playwright-cli 由 LLM agent 自由走查，无用例库指导、结果不可复现、失败后无法凭 trace 定位前后端归属；人工 E2E 走查的有效步骤没有沉淀为可重复资产。

## What Changes

- 引入前端 E2E 回归测试工程（独立 `e2e/` 目录：package.json + playwright.config.ts + tests/），不混入 Python `tests/`，质量门禁与 pytest 解耦
- 建立"录制 → 筛选 → 固化"工作流：用 playwright-cli 录制真实联调走查，agent 辅助把整段 TS 整理成 spec，人工 review 删改后作为确定性回归用例资产（.spec.ts）入库
- 真实联调运行形态：docker compose 起 mysql/redis/app/nginx，nginx 宿主端口映射由 80 调整为 8080（`8080:80`），Playwright `reuseExistingServer` 复用已起服务，`baseURL=http://localhost:8080`
- 测试侧 trace_id 打通：测试拦截每次响应头 `X-Trace-ID`，失败用例自动把 trace_id 附到测试报告，供 `/data/logs` grep 与 Langfuse 定位后端根因（配合 logging-convention-migration 的分层前缀）；不改前端 api.js/chat.js
- 首批用例覆盖聊天主链路（登录 → 新会话 → 选 KB → 提问 → SSE 流式渲染 → 工具状态 → 引用 → 历史会话）
- 录制/执行操作步骤沉淀进 `docs/agents/cookbook.md`（可复用操作流程，一事一档）

### 承接 P1 各 change 的人工 E2E/验收场景（KB 造数 + 后端日志断言）

真实联调运行形态为 P1 遗留的人工验证/人工 E2E 提供了**可复现的触发场景**，将下列验收以确定性用例（或受控冒烟）承接，跑通后在各旧 change 中交叉标注闭环：

- **chat-core 确定性回归**：登录 → 新建会话 → 选 KB → 提问 → SSE 流式渲染 → 工具状态 → 引用 → 历史会话（sse-tool-detail 3.2 的 detail + resume 属其中：断言状态标签文案含 `query=`/`queries=`，刷新后 resume 回放仍在）
- **行为验收（KB 造数 + 日志断言，冒烟级）**：agent-loop-hardening 5.3/4.5（绑 KB 无 [n] 被 KB 护栏引导补标 → 引用横条；态 A 纯对话不跑 judge；regen 后 search_web 完整执行不空白；缺年份→联网仍缺→标注"知识库与网络均未覆盖"）；检索行为信号（retrieval-quality-signals 4.2 / logging-convention-migration 4.3：真实 query 触发后日志出现对应 `retrieval_signal:` 行）
- **会话智能体与 skill 调用（`session-agent-and-skill-invocation` 的 8.4 / 8.5 / 8.8 / 8.9 / 8.10 / 8.11 / 8.12）**：新建对话选智能体 → 技能选择器选技能 → 第二轮仍受 skill 影响 → 刷新后顶栏回显（值来自 `sessions/list`）；一句话触发多个并行委派 → 事件/看板按 `delegate_id` 不串号；**绑定 KB + 选定智能体 → 答案仍带 `[n]`**（验证环境约束层未被 preset 覆盖，⚠ **须在 change `prompt-layering-and-domain-binding` 之后重跑** —— 该变更会把"环境约束层"重组为六段，验证对象是新写的）；子代理中途请求确认 → 澄清卡 → 答复后继续完成；绑定后传入不同 agent → 按绑定值生成、不报错，日志出现 `agent mismatch ignored`；`/xxx` 调 `context: fork` skill → `[n]` 有来源横条、抽屉可打开、日志无 `invalid_citation`；`scripts/migrations/2026-09-11-add-session-agent.sql` 幂等（列已存在时）+ 存量会话首次携带 agent 的 `bind-if-empty`
  - 交接日期 2026-09-18：该 change 的 8.6（prod compose 补 `agents/` 挂载）与 8.7（清理 `src/api/chat.py` 死代码）已由其**自行完成**，不在承接范围内

**承接方式**：测试内通过 document upload API **自建/自清受控 KB**（fixture），对"纯 UI 可断言"项以结构断言为主；对"后端事实"（judge 是否执行、search_web 是否执行、信号行是否出现）经测试捕获的 `X-Trace-ID` → 容器 `grep /data/logs` 做**后端日志断言**。依赖 LLM 随机行为的验收项按"受控 KB 尽力触发 + 结构断言 + 失败附 trace 转人工"标为冒烟级，不与确定性回归混在同一门禁语义。

## Capabilities

### New Capabilities

- `frontend-e2e-regression`: 前端 E2E 回归测试工程（e2e/ 目录、playwright 配置、与后端 compose 的联调运行方式、测试报告与运行命令）
- `e2e-recording-workflow`: playwright-cli 录制 → review 筛选 → spec.ts 固化的操作流程与用例组织约定（文件命名、断言规范、trace_id 标注）
- `e2e-trace-correlation`: 测试侧拦截 X-Trace-ID、失败自动附 trace_id 到报告的机制，打通"前端用例失败 → 后端 trace 定位"
- `e2e-acceptance-scenarios`: 受控 KB 造数 fixture + 后端日志断言钩子 + 验收场景集（chat-core 确定性回归 / 行为冒烟验收），承接 P1 各 change 的人工 E2E 待验项

### Modified Capabilities

- （无 — 不改既有 spec 级别行为；前端页面代码、后端 API 均无功能改动）

## Impact

- 新增 `e2e/`：package.json / playwright.config.ts / tests/（首批聊天主链路 spec.ts）/ fixtures
- 新增 `e2e/README.md`：环境前提、运行命令、录制工作流指引
- `docker-compose.override.yml`（或 compose 环境变量）：nginx 端口映射 `8080:80`（仅本地开发形态；生产部署端口不受影响）
- `docs/agents/cookbook.md`：追加"前端 E2E 录制与回归"操作记录
- 前端代码、后端代码：无改动（trace_id 走测试侧拦截）
- 测试：新增 e2e 独立测试，pytest 不受影响

## 依赖

本 change 的**日志格式断言依赖 `logging-convention-migration`**（格式契约方），分两级：

1. `[retrieval]` 事件与 `retrieval_signal:` 行——由 logging change **3.1 试点批已交付**（已合入），E2E 即刻可锚定
2. `[verify]` 层事件——logging change **3.2 尚未实施**（当前 verify 日志为自由文本、无 `[verify]` 前缀，作断言锚不稳定且将被 3.2 改写）→ 依赖 verify 层日志的用例（judge 是否执行、护栏引导等）在 **3.2 落地后**才有稳定锚点；落地前相关用例只以 UI/answer 产物断言、或显式标注"待 logging 3.2"

`X-Trace-ID` 关联本身不依赖本 change（trace_id 注入由现有 logging patcher/中间件保证，任何格式下均可 grep 回放）。
