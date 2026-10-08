## 1. 开工前置

- [ ] 1.1 提交本 change 的 ③④ 工件（`proposal.md` / `design.md` / `specs/`）到主工作区
- [ ] 1.2 按 `docs/agents/dev-flow.md` §⑤ 判据建隔离 worktree（**先提交、再建**），并按 `cookbook.md` 把 `.env` / `.venv` / `data/` 带过去
- [ ] 1.3 基线：在 worktree 跑一次全量 `POSTGRES_HOST=localhost python -m pytest tests/ -q` 记录基线

## 2. 摘要模块与持久化（不接入链路）

- [ ] 2.1 `src/config/settings.py`：新增 `SUMMARY_MODEL`（默认沿用 `LLM_MODEL`）；`src/models.py`：新增 `get_summary_llm()`——**必须 `enable_thinking: False` + `temperature=0` + 显式 `max_tokens`**（不设上限则"拒绝截断"不变量无从判定）
- [ ] 2.2 `src/config/const.py`：新增 `HISTORY_SUMMARY_TRIGGER_TOKENS = 12288`、**`SUMMARY_MAX_TOKENS = 2048`**（依据见 design「已闭问题 1」：参考 deepseek-harness 8192 / WeKnora <500 words / ragflow 1200 字符，再受本项目「摘要+尾部 ≤ 16384」聚合约束收敛）、摘要锁 TTL、摘要调用超时；注释写明"经验值、待标定"
- [ ] 2.3 `src/config/prompts/templates/`：新增摘要模板，**必须 `kind: task`**（勿用 `kind: section`——非法 section 值会让应用启动失败）、`id` 全局唯一；结构化段 `用户目标 / 已达成的决定 / 未解约束 / 关键事实与数字 / 下一步`；正文要求**「不超过 600 字」**、"不得输出方括号编号"、"在旧摘要基础上增量更新、不得比上一版更长"
- [ ] 2.4 跑 `python -c "from src.config.prompts import validation; validation.validate_all()"` 确认模板通过启动期校验
- [ ] 2.5 新增摘要模块（**不得往 `middleware.py` 加代码**）：**触发判据落在「与 `_truncate_history` 同口径算出的将被丢弃段」**（该段为空/未超阈值则不生成）、结构化产出解析、四条不变量校验（更小 / 严格不增长 / 按模型返回结束原因**拒绝截断** / 不做摘要之摘要）、`[数字]` 窄幅剥离、调用套 `wait_for` 超时
- [ ] 2.6 `src/chat/manager.py`：新增摘要 get/set/clear（Redis 键 `chat_summary:{session_id}`，TTL 同 `REDIS_TTL`，**写入续期**）——**三个方法各自都要写内存降级分支**（新增 `_memory_summaries`），否则无 Redis 环境会静默失效
- [ ] 2.7 摘要并发守卫：独立键 `chat_summary_lock:{session_id}`（**断言 ≠ `chat_lock:{sid}`**）、`SETNX` **带 TTL**、best-effort（拿不到直接跳过）
- [ ] 2.8 **启动期残留锁清理**：把 `chat_summary_lock:*` 纳入既有清理路径（否则进程被杀后该会话**永久无法生成摘要**）
- [ ] 2.9 `clear_history_async`：一并清除摘要与摘要锁（含内存分支）
- [ ] 2.10 新事件两处登记（`Event` 枚举 + `EVENT_SPECS`，prefix 均 `[session]`）：`summary done`（info，含覆盖条数/token 变化）、`summary fallback`（warning，含原因）
- [ ] 2.11 单测：**被丢弃段为空时不生成**（关键回归，防空转）；被丢弃段未超阈值不生成；超阈值生成并写回；锁争用跳过；**锁有 TTL 且启动清理覆盖**；四条不变量各自失败即不采用；超时降级；失败不重试且记降级；**内存降级路径**可用；窄幅剥离不误伤年份/链接且不丢弃整篇摘要；清空会话后摘要与锁均消失

## 3. 接入两段式（行为变化）

- [ ] 3.1 **回合正常完成后异步生成**：在 `src/services/turn_runner.py` 的 **else（正常完成）分支**、且 `add_message_async(assistant)` 成功后 → 判触阈（被丢弃段口径）→ best-effort 抢摘要锁 → **冻结当时的历史快照**作参数 → `asyncio.create_task` 生成并写回；**cancel / exception 分支 SHALL NOT 触发**；任务用模块级容器持强引用；SHALL NOT 延迟收尾或用户可见输出
- [ ] 3.2 **摘要值通道**：`src/services/agent_service.py` 的 `stream_chat`（已持 `chat_manager`）**预取**摘要 → 经 `launch_context` → `src/agents/graph/state.py` 新增字段传入；`agent_node` seed 点**只读该字段**构造独立 `HumanMessage`，**复用既有插入位置逻辑**（最后一个 `SystemMessage` 之后）；**不塞进 `state._history` 列表**
- [ ] 3.3 摘要段内容：可辨识标签 + 「仅供对话背景；事实性结论仍须以本轮检索结果为准；不确定时请用户复述」（依据 `retrieval-judgment`）；**不并入 system 段**
- [ ] 3.4 注入前**窄幅**编号处理：仅剥离/拦截形如 `[数字]` 的编号，**不因编号丢弃整篇摘要**（否则与触阈重生成形成反复空转）
- [ ] 3.5 **摘要段计入预算**：「摘要段 + 保留尾部」聚合 token ≤ `HISTORY_TOKEN_BUDGET`，超限先缩摘要、再裁尾部
- [ ] 3.6 无摘要/读取失败时：行为与本 change 之前**完全一致**（纯裁剪），不报错、不改顺序
- [ ] 3.7 回归：Phase A 的不变量未被破坏（预算口径不变、当前轮检索证据不被裁、`[n]` 仍指向本轮来源）；regen 轮不重复生成摘要（且复用首轮已注入的消息）

## 4. 文档与回归

- [ ] 4.1 `docs/agents/glossary.md`：新术语「跨轮摘要」「摘要覆盖边界」
- [ ] 4.2 `docs/agents/logging-rules.md`：登记 `summary done` / `summary fallback`
- [ ] 4.3 `docs/agents/code-map.md`：新增摘要模块、`get_summary_llm()`、摘要锁的落点
- [ ] 4.4 `python -m src.cli.check_docs` 与 `python -m src.cli.check_adr` 通过

## 5. 验收

- [ ] 5.1 `POSTGRES_HOST=localhost python -m pytest tests/ -v` 全绿；`ruff check .` 无错误；`pyright src/` 不新增 error
- [ ] 5.2 `openspec validate cross-turn-history-summary` 通过
- [ ] 5.3 端到端抽查（真实多轮中文会话，≥ 触阈轮数）：摘要生成并落 Redis；**触阈当轮注入不受影响**；**下一轮起**注入含摘要且顺序为「system → 摘要 → 尾部」；答案 `[n]` 仍指向本轮来源
- [ ] 5.4 对比证据：同一会话在「摘要在」与「摘要禁用」下的注入历史差异（证明由"丢弃"变为"摘要保留"）
- [ ] 5.5 专项验证：① 被丢弃段为空时不产生 LLM 调用（防空转）② 摘要锁泄漏后**不会**永久禁用该会话（重启清理生效）③ `GET /sessions/messages` 回放仍为**完整原文**、不含摘要
