# e2e-playwright-regression Design

## Context

前端是纯静态 HTML（`deploy/nginx/html/`，js/api.js 同源相对路径 `API_BASE='/api'`），由 nginx 同源反代到 FastAPI（app:8000）。联调问题只能真实浏览器交互暴露，pytest 覆盖不到。当前 playwright-cli 由 LLM agent 自由走查，无用例指导、不可复现、失败无法按 trace 归因。

调研结论（2026-09-03，web + Firecrawl 核读）：Slack 实测 200+ agentic E2E 显示 agent 驱动仅 ~20% 路径可复现、单次 $15-30 且成本随上下文重传累积——**agent 适合做探索/录制/诊断，确定性脚本适合做回归**。业界成熟范式 = spec-driven testing（playwright-cli 的 plan→generate→heal）与 trace 取证（截图 + console + network + trace 四件套 + `X-Trace-ID` 关联后端）。

技术栈约束：仓库无 node/前端构建链，Python 侧已有 pytest。前端页面目前 0 个 data-testid，依赖语义/角色定位。

## Goals / Non-Goals

**Goals:**
- 建立独立可提交的 `e2e/` Playwright 测试工程，覆盖真实联调回归
- 把 playwright-cli 的人工/agent 走查沉淀为确定性 spec.ts（录制→review→固化）
- 失败用例自动携带 `X-Trace-ID`，打通前端用例 → 后端 trace → Langfuse 的定位链
- 运行方式对现有 docker compose 形态改动最小（nginx 端口映射，生产不受影响）

**Non-Goals:**
- 不做 mock 后端的纯前端渲染回归（第一版只跑真实联调）
- 不改前端 api.js/chat.js、不改后端代码（trace_id 全在测试侧拦截）
- 不引入 md 用例库双轨维护（TS 即最终资产）
- 不引入 CI 编排、视觉回归 diff 工具（后续 change）
- 不迁移存量 239 处日志（属 logging-convention-migration）

## Decisions

### D1: E2E 测试工程形态 = 独立 e2e/ 目录（node + Playwright TS）

```
e2e/
├── package.json            # devDeps: @playwright/test；scripts.test = playwright test
├── playwright.config.ts    # testDir=tests；baseURL=http://localhost:8080；trace retain-on-failure
├── tests/
│   ├── chat-core.spec.ts   # 首批：聊天主链路（每条用例独立，自建数据自清理）
│   └── fixtures.ts         # 登录态复用 + trace_id 拦截注入
└── README.md               # 环境前提 / 运行命令 / 录制工作流
```

放独立 `e2e/` 而非 `tests/e2e`：现有 `tests/` 与 `src/` 模块一一对应且由 pytest 全量门禁驱动，混入 node 测试会污染 pytest 收集与门禁语义。e2e 属前端资产独立演进。

### D2: 真实联调运行形态 = 复用 docker compose，nginx 端口改 8080

nginx 容器内配置不变（`listen 80` + `location /api/ → app:8000` 是容器内部视角），宿主机端口映射 `80:80` → `8080:80`，Playwright `baseURL=http://localhost:8080`。理由：真实联调本来就需要 mysql/redis/app 全栈起着，nginx 只是其中一个服务；"解耦 nginx"是伪需求，nginx 也不限制必须绑 80。

实现：`docker-compose.override.yml` 中 nginx `ports: ["8080:80"]`（override 已存在，仅追加端口改写；生产 compose 不含 override 端口段，不受影响）。Playwright `webServer` 不自己起服务，用 `reuseExistingServer: true` + `command: true`（空操作），由测试者先 `docker compose up -d`。

### D3: 录制 → review → 固化工作流（spec-driven）

1. **录制**：playwright-cli 打开 `http://localhost:8080/chat.html`，走查主链路；每个动作输出等价 Playwright TS（`test-generation.md` 机制），agent 收集为 spec.ts 草稿并补产品级断言（toast/URL/列表项/气泡文本，非 DOM 细节）
2. **review**：人工/agent review 删改——删误操作与走错分支的步骤，保留正确操作与有效断言；错误目标可借此修正（fix locator/期望）
3. **固化**：审过的 spec.ts 提交入库，作为确定性回归资产，此后 `npx playwright test` 秒级重跑
4. **heal**：回归失败时 playwright-cli attach `--debug=cli` 会话诊断，区分"locator 漂移（改用例）/ 前端真改（改断言）/ 后端回归（报 bug + 附 trace_id）"

操作步骤沉淀进 cookbook.md（可复用操作流程，一事一档，不复制正文到其它文档）。

### D4: 测试侧 trace_id 关联（不改前端/后端）

Playwright 测试用 request/response 事件拦截每个响应头 `X-Trace-ID`（trace_id 中间件保证所有响应含该头，含 SSE）。用例内断言收尾时通过 fixture 在失败时把最近一次响应捕获的 trace_id 写入 testInfo.annotations / attachments。失败报告 = trace_id + 截图 + console + network（Playwright trace retain-on-failure）。

定位链：失败用例带出的 `trace_id` → `grep trace_xxx /data/logs`（分层前缀体系）→ 后端各层事件 + Langfuse。全在测试侧实现，前端零改动。

### D5: 首批用例范围 = 聊天主链路（真实联调，烧真实 LLM）

1. 登录（如 login.html 参与）→ 2. 新建会话 → 3. 选择知识库 → 4. 提问 → 5. SSE 流式 markdown 渲染完成 → 6. 工具状态事件（如检索/联网状态标签出现）→ 7. 引用呈现 → 8. 刷新后历史会话仍在。

断言策略：因 LLM 输出不可逐字复现，断言"结构特征"（消息气泡出现、markdown 渲染为富文本元素、状态标签文案来自 SSEInteractionTexts、done 后流结束、刷新后消息仍在）而非"与上次逐字一致"。每条用例独立自建会话/数据并在结尾清理，避免测试间污染。

### D6: 验收场景承接 = 受控 KB 造数 fixture + 行为冒烟用例

为承接 P1 遗留人工验收（agent-loop 5.3/4.5、sse 3.2、retrieval 4.2、logging 4.3），测试内通过后端 **document upload API 自建/自清受控 KB**（fixture helper：`ensureKb(name, docs[])` → 返回 kb_id，用例结束 `deleteKb`），对需触发 verify/护栏/联网的场景用受控文档内容（如只含部分年份、故意让答案漏 `[n]` 的编排）构造触发条件。

用例分层（不混在同一门禁语义）：
- **确定性回归层**（chat-core + sse detail/resume + 引用横条）：稳定结构断言，秒级重跑
- **行为冒烟层**（verify 护栏补标 / 态 A 不跑 judge / regen 后 search_web 执行 / 缺年份联网仍缺标注 / 检索行为信号行）：受控 KB 尽力触发，断言结构 + trace→日志；依赖 LLM 随机行为，允许失败转人工（失败自动附 trace_id、截图），不作为硬门禁

### D7: 后端事实 = 日志断言钩子（经 trace_id grep /data/logs）

UI 断言不到的后端事实（judge 是否执行、search_web 是否执行、`retrieval_signal:` 是否出现）由日志断言覆盖：用例捕获的 `X-Trace-ID` → 测试侧 helper 在 app 容器执行 `grep <trace> /data/logs/app_*.log`（`docker compose exec`）→ 断言事件行存在/缺失。该 helper 为测试侧工具，不改任何 src 代码。

**依赖（logging-convention-migration）分级**：`[retrieval]`/`retrieval_signal:` 锚点其 3.1 已交付、即刻可用；`[verify]` 层事件锚点其 3.2 尚未实施——verify 类日志断言项标注"待 logging 3.2 后启用"，落地前该类验收只以 UI/answer 产物断言或转人工。`X-Trace-ID` 关联本身不依赖该 change。

## Risks / Trade-offs

- [真实 LLM 输出不确定导致断言脆弱] → 断言只锚定产品级结构特征（SSEInteractionTexts 固定文案、元素存在性、流结束），不断言逐字内容
- [测试烧真实 LLM token/时间] → 首批用例数控制在小集；用例独立可单跑；后续可按 tag 分层（smoke/核心/全量）
- [无 data-testid，选择器靠语义/角色定位，UI 改动易漂移] → 断言优先产品语义文案；失败走 heal 流程而不是反复改断言；如 drift 频繁再评估补 data-testid（本 change 不动前端）
- [录制产物夹误操作/走错分支] → review 删改是流程强制步骤，spec.ts 未经 review 不入库
- [docker compose 8080 端口冲突或未起服务] → config `reuseExistingServer` + 明确 README 前提；测试启动前校验 baseURL 可达
- [trace 含业务/PII] → 仅本地联调环境，测试账号与数据最小化；trace 不上传外部
- [行为冒烟层依赖 LLM 随机性可能 flaky] → 与确定性回归分层；失败自动附 trace_id/截图转人工，不作为硬门禁
- [verify 层日志断言锚点在 logging 3.2 前不稳定] → 依赖项显式标注"待 logging 3.2"；落地前该类验收不写死 grep 文案锚

## Migration Plan

1. 先建 `e2e/` 工程骨架 + nginx 端口 override（本 change 代码交付）
2. 跑通一条录制→review→固化→重跑的 demo 用例验证闭环
3. 扩到首批聊天主链路用例集 + **受控 KB 造数 fixture 与后端日志断言 helper**
4. 按 D6 分层补验收场景用例（先确定性层 chat-core/sse/引用，后冒烟层 verify/信号，verify 日志类标注"待 logging 3.2"）
5. 跑通后回旧 change 交叉标注闭环；无生产迁移：后端/前端代码零改动；nginx override 仅本地形态，回滚 = 删除 override 端口段

## Open Questions

- 登录态是否纳入首批：login.html 是否参与主链路（若需要，D5 顺序前插登录步骤；复用 storageState 方案）
- 是否在本 change 一并加 npm scripts（`npm run e2e`）与 root README 指引，或仅在 e2e/README.md
- 冒烟验收用例的失败转人工流程：是否需要一个集中登记处（如 docs/agents/chunking-issues 式的验收台账）还是仅靠测试报告附件
