## 1. 开工前置

- [ ] 1.1 提交 ③④ 工件（本 change 目录 + `docs/adr/0017-*.md` + `docs/adr/README.md` + `docs/context-memory-research.md`）到主工作区；**只提交本次工件，不碰其他会话的暂存文件**（提交前 `git status` 确认）
- [ ] 1.2 从干净基点建隔离 worktree（`docs/agents/dev-flow.md` §⑤ 判据已满足），按 `cookbook.md`「并行会话（worktree）」把 `.env` / 依赖带过去
- [ ] 1.3 基线：在 worktree 跑一次全量 `POSTGRES_HOST=localhost pytest tests/ -v` 记录基线（确认哪些失败是环境性，如缺 gitignored `data/test_docs/*`）

## 2. 计数统一到分词器（行为变化）

- [ ] 2.1 新增统一计数入口（tiktoken `cl100k_base`，encoder 进程内缓存），供 `estimate_usage` 与历史裁剪共用
- [ ] 2.2 `src/infra/llm/token_usage.py:20-21`：`estimate_usage` 改用该入口；保留 `TokenUsage` 形状与 `total_tokens` 不变量
- [ ] 2.3 `src/agents/graph/agent_node.py:53,56`：`_truncate_history` 内联 `len//2` 改用同一入口
- [ ] 2.4 修正受影响既有测试：`tests/agents/graph/test_loop_middleware.py:550`（`usage["input"] == 9` 依赖 `len//2`）改为按新入口重算或断言不变量
- [ ] 2.5 新增单测：同一文本计数一致、空串边界、CJK 与英文各一例（记录 tiktoken 与 `len//2` 的比值，作为行为变化的证据）

## 3. 预算口径收口（行为变化）

- [ ] 3.1 `src/config/const.py`：新增 `HISTORY_TOKEN_BUDGET`（绝对值，保守初值），废弃 `HISTORY_TOKEN_RATIO` 的主口径地位
- [ ] 3.2 `src/agents/graph/agent_node.py`：`_truncate_history` 改为「轮数粗筛（`HISTORY_MAX_TURNS`）→ 绝对预算细裁」；**删除** `context_window` 写死默认值与 `token_ratio` 参数（或改为内部不暴露）
- [ ] 3.3 实现「最近 1 轮完整保留」的例外记录（含预算与实际用量，不静默超）
- [ ] 3.4 修正受影响既有测试：`tests/agents/graph/test_history_window.py:17,30,44`（显式传 `token_ratio=0.3, context_window=8000`）改为按新口径（绝对预算）断言
- [ ] 3.5 新增单测：裁剪后 ≤ 预算；超预算时逐条裁最旧；例外路径有日志
- [ ] 3.6 回归中文多轮用例，记录新预算下的实际保留条数（与旧口径对比，写入 change 说明）

## 4. 轮内累积度量（新增观测，不改内容）

- [ ] 4.1 在既有 model-call middleware 机制上度量模型调用前的输入 token（**不新增钩子类型、不触碰 `AgentSpanMiddleware` 末位守卫**）
- [ ] 4.2 达上限记 `used` / `limit` 日志（沿用 `Event`/`EventSpec` 注册与既有日志规范）
- [ ] 4.3 度量/日志失败降级：精确捕获 + warning + 原样放行（不被异常打断请求）
- [ ] 4.4 新增单测：度量触发日志；**断言超限不改变消息内容**（无截断/无删除）；异常注入时请求照常完成

## 5. 验收

- [ ] 5.1 `POSTGRES_HOST=localhost pytest tests/ -v` 全绿；`ruff check .` 无错误；`pyright src/` 不新增 error
- [ ] 5.2 `openspec validate context-window-and-budget-foundation` 通过；`python -m src.cli.check_adr` 0 error
- [ ] 5.3 文档同步：`docs/agents/glossary.md`（新术语：历史预算 / 轮内累积）、`docs/agents/code-map.md`（若新增模块）、`docs/agents/api_contract.md`（若签名变更）
- [ ] 5.4 登记明确不做项与已知遗留：「触顶 → 空回答」（F-37）不修；per-model 注册表/探针/窗口解析、工具结果治理**移入 Phase B**，在需求池或 change 说明中交叉引用
- [ ] 5.5 端到端抽查：一次真实中文多轮对话，核对日志中的历史 token 用量 ≤ 预算、轮内度量信号出现、Langfuse token 记录与度量一致
