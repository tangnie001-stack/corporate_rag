# e2e-playwright-regression Tasks

## 1. E2E 工程骨架

- [ ] 1.1 创建 `e2e/` 目录结构（package.json + playwright.config.ts + tests/ + README.md），package.json 声明 `@playwright/test` devDependency 与 `test`/`test:headed` 等 scripts
- [ ] 1.2 `e2e/playwright.config.ts`：`testDir=tests`、`baseURL=http://localhost:8080`、`webServer` 复用已起服务（`reuseExistingServer:true`，不自己拉起）、`trace:'retain-on-failure'`、截图 `only-on-failure`
- [ ] 1.3 `e2e/README.md`：环境前提（docker compose 全栈、nginx 8080 映射）、安装/运行命令、录制工作流指引
- [ ] 1.4 `e2e/.gitignore`（node_modules、test-results、playwright-report）；根 .gitignore 如有需要同步
- [ ] 1.5 `npm install` 验证依赖可安装，`npx playwright install chromium`（如需浏览器）可执行

## 2. 运行形态（nginx 8080 映射）

- [ ] 2.1 `docker-compose.override.yml` 追加 nginx `ports: ["8080:80"]`（仅本地形态，不影响生产 compose）
- [ ] 2.2 验证：docker compose 全栈起后 `http://localhost:8080/chat.html` 可访问、页面 `/api` 请求经反代成功（浏览器控制台无 404/跨域）
- [ ] 2.3 冒烟：写一条最小 spec（打开 chat.html、断言标题可见）跑通 `npx playwright test`，确认连接 8080 与报告产出

## 3. trace_id 测试侧拦截

- [ ] 3.1 `e2e/tests/fixtures.ts`：fixture 内拦截响应，读取 `X-Trace-ID` 头并存入当前用例上下文
- [ ] 3.2 失败自动附 trace_id：断言失败时把最近一次 trace_id 写入 `testInfo.annotations`（attachment）
- [ ] 3.3 验证：故意制造一次失败用例，确认报告里出现 trace_id + 截图 + trace；用该 trace_id 能在 `/data/logs` grep 到后端日志行
- [ ] 3.4 确认前端 `api.js`/`chat.js` 与后端代码零改动（只读校验）

## 4. 录制 → review → 固化工作流验证（demo 用例）

- [ ] 4.1 用 playwright-cli 走查一条真实聊天流程，收集生成的 TS 拼成 spec 草稿
- [ ] 4.2 review 删改草稿：去除误操作/无效步骤，保留正确操作并补产品级断言（消息气泡、状态标签、引用呈现等）
- [ ] 4.3 固化 demo 用例到 `e2e/tests/`，`npx playwright test` 单跑通过
- [ ] 4.4 操作步骤沉淀到 `docs/agents/cookbook.md`（"前端 E2E 录制与回归"条目，一事一档，不复制到其它文档）

## 5. 首批聊天主链路用例集

- [ ] 5.1 编写聊天主链路用例：新建会话 → 提问 → SSE 流式渲染完成 → 工具状态事件出现 → 引用呈现（每用例独立自建/清理）
- [ ] 5.2 补充历史会话用例：刷新页面后历史会话与消息仍在（视登录态形态决定是否前置登录步骤）
- [ ] 5.3 全套 `npx playwright test` 通过；确认断言锚定结构特征而非逐字 LLM 输出
- [ ] 5.4 回归：`pytest tests/ -v` 全过（不受 e2e 引入影响）；`ruff check .` 无错误

## 6. 验收场景承接（KB 造数 fixture + 后端日志断言 + 冒烟用例）

- [ ] 6.1 fixture：`ensureKb(name, docs[])` / 清理 helper——用例内经 document upload API 自建/自清受控 KB（含触发 verify/护栏/联网的内容编排）
- [ ] 6.2 后端日志断言 helper：用例捕获的 `X-Trace-ID` → 容器 `grep <trace> /data/logs/app_*.log` → 断言事件行存在/缺失；`[verify]` 层锚点标注"待 logging 3.2"，`[retrieval]`/`retrieval_signal:` 即刻可用
- [ ] 6.3 确定性回归层：chat-core 主链路（新建会话/选 KB/提问/SSE 渲染/状态/done）+ **sse-tool-detail 3.2**（status detail 文案含 query=/queries=，刷新 resume 仍在）+ **agent-loop 4.5**（引用来源按钮/横条）
- [ ] 6.4 行为冒烟层（受控 KB 触发，断言结构 + trace→日志，失败自动附 trace_id 截图转人工，不作硬门禁）：**agent-loop 5.3**（绑 KB 无 [n] 被护栏补标；态 A 不跑 judge——judge 断言待 logging 3.2；regen 后 search_web 完整执行；缺年份联网仍缺→标注"知识库与网络均未覆盖"）
- [ ] 6.5 检索行为信号冒烟：一条真实 query 触发后日志出现对应 `retrieval_signal:` 行且可按 trace 回放（承接 retrieval-quality-signals 4.2 / logging-convention-migration 4.3）
- [ ] 6.6 各场景稳定通过后，回 agent-loop-hardening / retrieval-quality-signals / sse-tool-detail / logging-convention-migration / agent-harness 遗留对应 tasks 交叉标注闭环
- [ ] 6.7 回归：`pytest tests/ -v` 全过（不受 e2e 引入影响）；`openspec validate` 通过

## 7. 验证与收尾

- [ ] 7.1 失败取证端到端验证：人为制造一次前端/后端失败，确认 trace_id → 日志 → Langfuse 定位链走通
- [ ] 7.2 `openspec validate` 通过
- [ ] 7.3 检查本文档勾选状态与真实文件一致（以代码为准，不盲信勾选）
