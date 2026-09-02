# agent-harness-foundation Proposal

## Why

当前项目定位为"Corporate RAG 问答系统"，但产品目标是把系统建成**企业智能助手 harness**：聊天底座 + 可插拔能力（知识库/RAG 是其中一个通过工具加载的能力），后续可外挂其他知识库或功能（tools/MCP）。同时，"腾讯这几年业绩只答 2024"这一线上问题暴露了编排工程缺失——无验证循环（答案生成后无人校验完整性）、无时间结构化约束（"这几年"由 LLM 自由理解）、工具系统硬编码 3 个固定工具（无法外挂能力）。本轮先做单 Agent 阶段的 harness 基础（P0），多 Agent/subAgent 留待单 Agent 调通后（P2）。

## What Changes

- **CLAUDE.md 认知层调整**：标题由 `# Corporate RAG` 改为 `# Corporate Agent Harness`；角色描述改为"负责企业智能助手 harness（聊天底座 + 可插拔工具/知识库）的设计，RAG 知识库是其中一个能力模块"；技术栈补充 MCP；`docs/agents/reference-projects.md` 中 harness 类参考项目（claude-code / deepseek-harness / codex）的"何时查阅"优先级提前
- **时间结构化约束（新增能力）**：候选年份由绑定 KB 文档元数据（`meta_info` 的 year/report_period 实体）**∪ 最近 3 个完整年度**派生（季度/半年度粒度归一为年份），**时间词正则粗筛命中才调 LLM** 从候选集合中选择（而非自由生成），代码层校验合法性；缺失年份由代码比对算出，作为**询问用户是否联网补充**的依据（用户确认后才联网）
- **验证循环 / 答案校验（新增能力）**：在 `agent_finalize` → `format` 之间新增校验节点，完整性校验用**正则提取答案年份**比对要求范围，忠实度校验复用 `RAGAS_LLM_MODEL`（temperature=0 独立评估模型）做 LLM judge（judge 仅在完整性通过的最终答案运行，打回信号仅记录供 P1 输出护栏）；**缺失年份经 ask_user 询问用户"是否联网"确认后才补充**（确认在**单轮请求内**记住，跨轮持久化留 P1），确认后向 messages 注入 SystemMessage 驱动 agent 调 search_web 重生成，verify 自查 `_agent_iterations` 超限转标注缺失直通（软拒答，转人工留 P1）
- **工具注册表化（新增能力）**：`make_rag_tools` 硬编码工具列表升级为 `ToolRegistry`（注册 / 启用开关 / 依赖注入，deps 字段留 MCP 接入启用），每个工具独立 handler；**KB=RAG 开关**——会话未绑定 KB 时不调 retrieve_kb，落地为工具内空返回（删 `_semantic_select_kb` 隐式跨库）+ prompt 软引导（graph 编译期工具列表固定，无法按会话隐藏，per-session 裁剪留 P1）；`kb_id=""` 语义改为"不检索"（**废弃隐式跨库检索**）；为 MCP 接入与未来 subagent 工具（多 Agent 阶段）留接口
- **聊天页 UI 重构（新增能力）**：chat.html 按 `docs/design/pages/chat-harness.md` 设计稿重构——布局参考 deepseek-harness（左侧会话栏 + 双页面形态：新对话页居中输入框 / 历史对话页底部输入框），**整体浅色系（与现有 chat.html / MASTER.md 一致）**；知识库（KB）选择器置于新对话页输入框上方独立行（单选勾选、可取消，取消显示"请选择知识库"），KB 会话级绑定（新建会话时选定，会话内不可改，历史对话页顶栏显示 `会话名 知识库:XXX`）；消息流参照现有 chat.html（用户浅灰气泡 / AI markdown 回复 + 模型标注，无时间戳）

## Capabilities

### New Capabilities
- `temporal-constraint`: 相对时间表达的结构化解析与缺失年份判定，约束 LLM 对时间词的解析
- `answer-verification`: 答案生成后的完整性/忠实度校验循环，校验不通过触发修订
- `tool-registry`: 可插拔工具注册表，统一工具注册/启停/依赖注入，支持扩展能力与 MCP 接入
- `chat-harness-ui`: 聊天页 UI 重构（布局/知识库选择器/消息流样式），设计稿 `docs/design/pages/chat-harness.md`

### Modified Capabilities
- 无（现有 spec 的 requirement 不改变；`kb-routing` 下移为工具内部逻辑属 P1，不在本 change）

## Impact

- **代码**：`src/rag/temporal.py`（候选年份派生/时间解析）、`src/agents/graph/verify_node.py` 与 `workflow.py`（验证循环节点）、`src/agents/tools/rag_tools.py`（工具注册表化 + 删语义选库）、`src/config/prompts.py`（系统提示词调整）、`deploy/nginx/html/chat.html`（聊天页 UI 重构）
- **文档**：`CLAUDE.md`（定位/角色/技术栈/目录结构）、`docs/agents/reference-projects.md`（参考项目优先级）、`docs/agents/glossary.md`（如有新术语）、`docs/design/pages/chat-harness.md` 与 `docs/design/chat-harness-mockup.html`（UI 设计稿）
- **测试**：新增时间解析、验证循环、工具注册表单测；存量 retrieval / tools / agent 相关测试适配；UI 用 playwright-cli 验证设计稿与 chat.html
- **明确不做（本 change 范围外）**：长期记忆（短期 Redis 会话已够，且与 RAG 溯源原则冲突）、kb_router 下移（P1 与验证循环的图改动合并）、Multi-Agent/subAgent（P2）
