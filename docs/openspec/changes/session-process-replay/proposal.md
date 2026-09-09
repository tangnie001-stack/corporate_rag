# Proposal: session-process-replay

## Why

AI 回答的过程叙事（状态行、思考块、旁白、委派分析、检索/联网动作序列）目前只在浏览器运行期存在——SSE 帧被前端现场聚合渲染后即丢弃，服务端不累积，刷新/切会话/隔天打开后全部消失（设计决策 D7「历史重载不重建，仅展示终稿」）。用户要求过程成为回答的一部分：**刷新后要跟当时看到的一致**。同时发现两个相关缺陷需要一并修复：深度思考关闭时模型的规划文本（旁白）泄漏进 content 通道、被无差别粘进正文气泡；深度思考开启时多轮工具调用的 `reasoning_content` 未回传（LangChain 标准序列化丢弃非标字段），工具调用准确性静默下降（官方文档明确要求回传）。

## What Changes

- **过程事件采集**：`_run_generation` 事件循环新增私有事件日志（`_StreamCapture.events_log`），每个 SSE 事件按到达顺序 append（独立于 StreamingRunManager 的 2000 帧截断缓冲，无上限）
- **终态序列化落库**：生成收尾把事件日志按轮次语义分拣（tool_calls 收尾轮的 content token 帧保留为旁白；末轮 answer 的 token 帧剔除，避免历史回放正文重复；model_info/abstention/done/error/citation 五类帧不入 process）后整体序列化为 `{"format_version": 1, "events": [{seq, type, payload}, ...]}` JSON，写入 `conversation_history` 新列 `process`（MEDIUMTEXT NULL）；模型名复用既有 `model_name` 列透传；取消/超时路径存到中断点为止
- **messages 接口扩展**：`MessageItem` 返回 `process`、`model_used`（存量数据为 NULL，前端判空兼容）
- **前端双路径渲染统一**：历史回放把 `process` 数组按序喂给与实时路径相同的渲染管线（同一套聚合逻辑）；新增旁白块（`token_preamble`，虚线弱化样式，与正文分置于过程容器）；相邻同型元素合并为组 div（只看相邻，不看全局）；**D7「历史重载不重建」正式反转**
- **实时路径旁白隔离**：首个工具调用之前的 token 进待定缓冲，工具调用出现后固化为旁白块；直到 done 均无工具调用则待定区即正文倒入气泡（修复旁白粘进正文开头）
- **reasoning_content 回传**：自定义 ChatOpenAI 序列化，assistant 消息携带 `reasoning_content`，请求带 `preserve_thinking=true`（qwen3.7-flash 在官方支持列表）
- **表结构迁移**：`ALTER TABLE conversation_history ADD COLUMN process MEDIUMTEXT NULL, ADD COLUMN model_used VARCHAR(64) NULL`（存量不回填）

## Capabilities

### New Capabilities
- `session-process-replay`: 过程轨迹的采集（生成期事件按序记录）、持久化（终态写入 process 列）、查询（messages 接口返回）与回放（前端按序重建过程容器，与实时路径同一渲染管线）；含旁白判定规则（最后一次工具调用之后的 content 流为正式回答，之前为旁白）与相邻同型合并渲染规则

### Modified Capabilities
- `chat-message-model`: assistant 消息新增 `process`（过程事件 JSON）与 `model_used` 两个可空字段的持久化与读取要求
- `chat-harness-ui`: 历史回放从「仅终稿」改为「完整过程重建」（D7 反转）；新增旁白块样式与相邻同型合并渲染规则；实时路径旁白与正文分流

## Impact

- **代码**：`src/agent_service.py`（_run_generation 循环 + capture）、`src/chat/persistence.py`（落库）、`src/infra/db/models/chat.py`（MessageModel）、`src/api/sessions.py`（MessageItem）、`src/infra/llm/` 或 `src/agents/`（ChatOpenAI 序列化子类）、`deploy/nginx/html/chat.html`（渲染管线）
- **数据库**：`conversation_history` 表 ALTER TABLE 加 process 列（手动执行，登记 cookbook）
- **API 契约**：`/api/sessions/messages` 响应新增字段（api_contract.md 登记）
- **文档**：glossary（旁白术语、D7 反转）、cookbook（ALTER 操作，帧级核对已登记）、chat-harness.md / MASTER 设计规格同步
- **设计稿**：`docs/design/chat-eventstream-mockup-2026-09-08.html`（v2）为渲染层验收基准——真实帧序（trace_3157b559）、旁白块样式、相邻合并组结构均以该稿对照
- **依赖**：无新增外部依赖；`preserve_thinking` 为 DashScope 平台参数（qwen3.7-flash 支持）
