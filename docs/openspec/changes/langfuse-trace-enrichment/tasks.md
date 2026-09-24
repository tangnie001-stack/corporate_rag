## 1. 模型单价配置与定价写入

- [ ] 1.1 `src/config/settings.py` 新增模型输入 / 输出单价字段（env 驱动，默认 0 表示「未配置」），并同步 `.env.template` / `.env.example` 的注释说明
- [ ] 1.2 新增 `src/cli/seed_langfuse_models.py`：用 `langfuse.api.models` 的 `list` + `create` 幂等写入模型定价；**单价为 0 时跳过并打印提示**，不写入零价模型定义
- [ ] 1.3 CLI 单测：未配置时跳过、重复执行不产生重复模型定义、配置后按 `match_pattern` 命中

## 2. 工具观测（核心）

- [ ] 2.1 新增 `src/infra/llm/tool_trace.py`：`_ToolTraceCollector`（`_enabled` 守卫、`{run_id: span}` 账本、当前 `tools` 父 span、`consume(item)` 三分支、`close()` 兜底）
- [ ] 2.2 父 span 的开关接节点级 `on_chain_start/end`（`name == "tools"`）；工具 span 以 `parent_observation_id` 挂在其下
- [ ] 2.3 工具 span 一律用 `Langfuse.span(trace_id=…)` 创建（**不调用 `client.trace()`**）；`_on_end` 从 `ToolMessage` 显式取 `.content` / `.tool_call_id` / `.name` 后再写入
- [ ] 2.4 `src/services/agent_service.py` 的事件循环内挂 `tool_trace.consume(item)`（仅 `scope == "main"`），并在 `finally` 调用 `close()`
- [ ] 2.5 预留三个 hook 的位置（入参归一化 / 输出摘要 / 来源标记）—— 仅把写死的行抽成小函数，**不实现逻辑**
- [ ] 2.6 采集器单测：`run_id` 配对、`on_tool_error` 错误态、取消路径 `close()` 关闭未结束 span、`LANGFUSE_ENABLE=false` 时零产出

## 3. generation 载荷与输出

- [ ] 3.1 `src/agents/graph/message_payload.py`：`role` 由 LangChain 类型名规范化为 OpenAI 形态（`ai`→`assistant`、`human`→`user`，`system`/`tool` 原样），assistant 条目补 `tool_calls`、tool 条目补 `name`
- [ ] 3.2 `src/agents/graph/agent_node.py`：generation 的 `output` 改为文本优先 —— 文本非空写文本，文本为空且存在 `tool_calls` 时写 `{"tool_calls": […]}`；仍保持 `capture_input=False` + 显式写入
- [ ] 3.3 同步既有断言：`tests/agents/graph/test_agent_node_tracing.py`、`tests/services/test_run_generation_tracing.py` 及 `_messages_payload` 相关用例

## 4. trace 富化

- [ ] 4.1 `_run_generation` 的 `update_current_trace` 扩参：`user_id`（`current_user_id`）、`tags`（`chat` + `kb`|`no_kb`）、`metadata`（agent / agent_display_name / skill_action / loaded_skills / kb_id / kb_domain / deep_thinking / direct_skill / has_skills）——**全部只读 `RequestContext`**
- [ ] 4.2 单测：字段取值正确；**高基数取值（kb_id / agent / skill）进 metadata 而非 tags**

## 5. 文档与 ADR

- [ ] 5.1 新增 ADR：引入命令式 Langfuse client 接入（与 `llm-tracing` 既有「纯装饰器」决策的取舍），并在 `docs/adr/README.md` 索引表登记
- [ ] 5.2 `docs/agents/code-map.md` 登记 `src/infra/llm/tool_trace.py` 与 seed CLI 的落点；`docs/agents/cookbook.md` 补「seed 模型定价」的操作步骤
- [ ] 5.3 更新 trace 记录范围的文档说明（新增工具 span 与载荷字段，含「子代理工具 / 工具内部子步骤不在事件流内」这一已知边界）

## 6. 验证

- [ ] 6.1 质量门禁全绿：`pytest`（宿主侧带 `POSTGRES_HOST=localhost`）/ `ruff check .` / `pyright src/` / `check_docs` / `check_adr`
- [ ] 6.2 关闭态回归：`LANGFUSE_ENABLE=false` 跑一轮对话 → Langfuse 零新增、SSE 事件序列与开启态一致
- [ ] 6.3 dev E2E：一轮含 ≥3 次迭代、≥2 次工具调用 → 每轮一条 `tools` 父 span + 逐工具子 span（含工具名 / 入参 / 返回 / 耗时）、工具轮 `output` 非空、`user_id` / `tags` / `metadata` 落库
- [ ] 6.4 取消路径 E2E：生成中取消 → 已开启的工具 span 均被关闭，无悬空节点
- [ ] 6.5 填单价并 seed 后确认 `totalCost > 0`；未配置时确认成本为空且无 0 价模型定义
- [ ] 6.6 `openspec validate --changes langfuse-trace-enrichment` 通过
