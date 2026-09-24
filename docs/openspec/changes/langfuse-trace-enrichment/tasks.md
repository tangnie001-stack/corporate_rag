## 1. 模型单价配置与定价写入

- [ ] 1.1 `src/config/settings.py` 新增模型输入 / 输出单价字段（env 驱动，默认 0 表示「未配置」），并同步 `.env.template` / `.env.example` 的注释 —— **注释必须写明价格单位是 USD**（Langfuse 的 `input_price` / `output_price` 以美元计；要人民币口径须给换算关系，不能直接填人民币数值）
- [ ] 1.2 新增 `src/cli/seed_langfuse_models.py`：用 `langfuse_context.client_instance.api.models` 的 `list`（**翻页遍历**）+ `create` 幂等写入；三个字段写死 —— `model_name=qwen3.8-flash`、`match_pattern=(?i)^qwen3\.8\-flash$`、`unit=TOKENS`；**单价为 0 时跳过并打印提示**，不写入零价模型定义
- [ ] 1.3 CLI 单测：未配置时跳过；重复执行不产生重复模型定义；**断言 `match_pattern` 真能匹配 `qwen3.8-flash`**（含一条反例：未锚定的正则匹配不到 —— 这正是「seed 成功但成本仍为 0」的静默失败来源）

## 2. 工具观测（核心）

- [ ] 2.1 新增 `src/infra/llm/tool_trace.py`：`_ToolTraceCollector`（`_enabled` 守卫、`{run_id: span}` 账本、当前 `tools` 父 span、`consume(item)` 三分支、`close()` 兜底）
- [ ] 2.2 父 span 的开关接节点级 `on_chain_start/end`（`name == "tools"`）；工具 span 以 `parent_observation_id` 挂在其下
- [ ] 2.3 过滤判据**只用 `metadata.langgraph_node == "tools"`**；**不得**用 `checkpoint_ns` 判空 —— 实测 `on_tool_*` 的 `checkpoint_ns` 是 `tools:<uuid>`（非空），误用会**丢掉全部工具事件**
- [ ] 2.4 工具 span 一律用 `langfuse_context.client_instance.span(trace_id=…)` 创建（**不调用 `client.trace()`**；**不 new `Langfuse()`**，否则绕过开关与 flush）；`_on_end` 从 `ToolMessage` 显式取 `.content` / `.tool_call_id` / `.name` 后再写入
- [ ] 2.5 `src/services/agent_service.py` 的事件循环内挂 `tool_trace.consume(item)`，并在 `finally` 调用 `close()`
- [ ] 2.6 预留三个 hook 的位置（入参归一化 / 输出摘要 / 来源标记）—— 仅把写死的行抽成小函数，**不实现逻辑**
- [ ] 2.7 采集器单测：`run_id` 配对；`on_tool_error` 错误态；取消路径 `close()` 关闭未结束 span；`LANGFUSE_ENABLE=false` 时零产出；**同一轮并行多个工具时各自成 span 且都挂在同一条父 span 下**

## 3. generation 载荷与输出

- [ ] 3.1 `src/agents/graph/message_payload.py`：`role` 由 LangChain 类型名规范化为 OpenAI 形态（`ai`→`assistant`、`human`→`user`，`system`/`tool` 原样），assistant 条目补 `tool_calls`、tool 条目补 `name`
- [ ] 3.2 `src/agents/graph/agent_node.py`：generation 的 `output` 改为文本优先 —— 文本非空写文本，文本为空且存在 `tool_calls` 时写 `{"tool_calls": […]}`；仍保持 `capture_input=False` + 显式写入
- [ ] 3.3 同步既有断言：`tests/agents/graph/test_agent_node_tracing.py:11,24`（断言 `"human"` 与键集 `{role, content}`）、`tests/services/test_run_generation_tracing.py`、以及 `message_payload.py` 的返回类型注解

## 4. trace 富化

- [ ] 4.1 `_run_generation` 的 `update_current_trace` 扩参：`tags`（`chat` + `kb`|`no_kb`）、`metadata`（agent / agent_display_name / skill_action / loaded_skills / kb_id / kb_domain / deep_thinking / direct_skill / has_skills，**只读 `RequestContext`**）；`user_id` **在请求内捕获后显式传入**（与 `trace_id` 同做法，不依赖 contextvar 继承），并**订正 `src/api/chat.py:42-43` 那条「任务与请求不共享 context」的误注释**
- [ ] 4.2 单测：字段取值正确；**高基数取值（kb_id / agent / skill）进 metadata 而非 tags**；**集成断言：后台任务内 `user_id` 确实非空**（防「静默变空」回归）

## 5. 文档与 ADR

- [ ] 5.1 新增 ADR：引入命令式 Langfuse client 接入（含「client 实例统一取 `langfuse_context.client_instance`」的口径，与 `llm-tracing` 既有「纯装饰器」决策的取舍），并在 `docs/adr/README.md` 索引表登记
- [ ] 5.2 `docs/agents/code-map.md` 登记 `src/infra/llm/tool_trace.py` 与 seed CLI 的落点；`docs/agents/cookbook.md` 补「seed 模型定价」的操作步骤（含 USD 口径与 `match_pattern` 说明）
- [ ] 5.3 trace 记录范围的说明**落在 `docs/agents/` 的常驻档或本次新增的 ADR 里**；**不得原地修改已接受的 ADR（如 ADR-0012）正文**，若确需改其口径，按既有约定走「追加新 ADR + 旧 ADR 加 Status 反向指针 + 索引表登记」。内容须含：新增工具 span 与载荷字段，以及「fork 子代理工具（回调被 `executor.py:189` 切断）与工具内部子步骤不在主事件流内」这一已知边界

## 6. 验证

- [ ] 6.1 质量门禁全绿：`pytest`（宿主侧带 `POSTGRES_HOST=localhost`）/ `ruff check .` / `pyright src/` / `check_docs` / `check_adr`
- [ ] 6.2 关闭态回归：`LANGFUSE_ENABLE=false` 跑一轮对话 → Langfuse 零新增、SSE 事件序列与开启态一致
- [ ] 6.3 dev E2E：一轮含 ≥3 次迭代、≥2 次工具调用 → **工具 span 数量 == 实际工具调用次数**（防过滤判据写错导致零 span）、每轮一条 `tools` 父 span + 逐工具子 span（含工具名 / 入参 / 返回 / 耗时）、工具轮 `output` 非空、`user_id` / `tags` / `metadata` 落库
- [ ] 6.4 取消路径 E2E：生成中取消 → 已开启的工具 span 均被关闭，无悬空节点
- [ ] 6.5 **必过闸门**（不得降级为可选）：填单价（USD 口径）并 seed 后，≥1 条 generation 的 `totalCost > 0`；未配置时确认成本为空且无零价模型定义
- [ ] 6.6 `openspec validate --changes langfuse-trace-enrichment` 通过
