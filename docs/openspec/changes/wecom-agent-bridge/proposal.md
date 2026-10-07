## Why

企业微信三台长连接机器人已上线（`wecom-multi-bot`），但 `wecom_service` 的 handler **只回占位文案**——站点侧 Agent 的全部能力（知识库检索、联网搜索、澄清追问、子代理委派、引用来源）在企微**完全不可达**。需要把企微接入**站点同款 Agent 管线**，让企微用户获得与站点一致的 Agent 问答体验。

这不是"接检索"，而是**通道适配 + 输出投影**：Agent 是否检索、是否联网、是否澄清，全部由 Agent 自主决策（与站点一致）；本 change 只负责把入站消息喂进那条管线、并把管线的富输出投影进企微能渲染的子集。

## What Changes

- **通道 → Agent 桥接**：入站消息推导 **36 字符的确定性 `session_id`（UUIDv5）**（群聊按 `chatid`、单聊按 `from_userid`）→ `msgid` 去重 → 生成 `trace_id` → 调用与站点同一条生成管线（`agent_service`）。
- **长连接驱动可靠性补丁**：认证等待（当前 `connect()` 不等认证即返回）、被顶号即停重连（防互踢）。
- **企微流式输出投影**：把 Agent 的 SSE 事件流投影为 `reply_stream`——**累积全量快照**语义、节流发送、帧数与长度上限、**长流 4min 保活**（防 6min 超时）；占位首帧；引用降级为文末「参考来源」；空回答/拒答兜底收尾；错误脱敏；`reasoning`/`delegate`/`task`/`model_info` 等无渲染通道的事件丢弃。
- **澄清回填**：`ask_user` 挂起期间在企微侧呈现为**一条编号问题**；**仅触发澄清者**的回复回填 `pending_asks`（本轮以文本回填为主；卡片留后续）。
- **trace_id 三路返回**：`feedback.id`（首帧设置，供用户反馈回传）+ 日志锚点 + 终态可见 footer（**受配置开关控制；反馈标识不可用时强制开启**）。
- **编排下沉（不改站点行为）**：把 `src/api/chat.py` 的"跑一轮 + 落库 + 终态"编排提取到 `services/`，供站点与通道共用（层间规则：`channels` 不得 import `api`）。

## Capabilities

### New Capabilities
- `wecom-agent-bridge`: 通道侧桥接（**仅长连接路径**）——入站消息推导会话（UUIDv5）与身份、`msgid` 去重、`trace_id` 生成与三路返回、把消息喂进站点同款 Agent 管线、澄清答案回填（仅触发者 / 一问一答），以及驱动可靠性（认证等待 / 被顶停重连）。
- `wecom-stream-projection`: 把 Agent 事件流投影为企微流式帧——快照式累积、节流、帧数/长度上限、长流保活、占位首帧、引用降级、无通道事件丢弃、终态收尾（含 trace_id footer、空回答兜底、错误脱敏）。

### Modified Capabilities
- `wecom-channel`: 将「每台机器人一条长连接驱动，逐台装配、降级与关停」中"连接成功"的判定由"建立连接"改为"**建立连接并完成认证**"，锚点日志口径相应改为"认证成功台数"，并新增"认证失败不计入成功"场景（驱动侧"认证失败即断开且不重连"的行为规范归新能力 `wecom-agent-bridge`，不在此重复）。

## Impact

- **代码**：
  - `src/channels/wecom/`：新增 `handler.py` / `presenter.py` / `dedup.py`；改 `long_connection.py`（认证等待 / 被顶停重连）。
  - `src/channels/base.py`：`ReplySink.reply_stream` 增加**可选**反馈参数（承载 `trace_id`），不破坏既有回调驱动实现（不做能力位声明——无消费者，见 design D13）。
  - `src/services/`：`wecom_service.py` 的**长连接** handler 由占位换成桥接 handler（`callback` 保持占位）；新增共用编排（自 `api/chat.py` 下沉）。
  - `src/api/chat.py`：编排改为调用下沉后的 service 函数（外部行为不变）。
- **前置依赖**：`wecom-multi-bot` 已归档（`openspec/changes/archive/2026-10-07-wecom-multi-bot/`），其能力已提升为主规格 `openspec/specs/wecom-channel/spec.md`（多机器人装配/降级/关停与入站分发）；本 change 建立在其上。对其的**修改**见「Modified Capabilities」（仅收紧装配/降级/锚点台账口径，驱动层行为规范归新能力）。
- **依赖行为**：官方 `aibot` SDK（`WSClient.reply_stream` / `feedback` / 事件订阅）；其快照语义、认证时机、被踢行为须经 Spike 实证（见 design 与 tasks 组 0）。
- **文档**：`docs/agents/defensive-patterns.md`（SDK 补丁与投影层防复发）、`docs/agents/code-map.md`（通道层落点）。
- **风险**：编排下沉触及站点生成路径，须以"站点行为不变"为准绳并跑回归（`tests/` + 前端 SSE 冒烟）。
