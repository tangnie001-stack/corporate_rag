## Why

企业微信三台长连接机器人已上线（`wecom-multi-bot`），但 `wecom_service` 的 handler **只回占位文案**——站点侧 Agent 的全部能力（知识库检索、联网搜索、澄清追问、子代理委派、引用来源）在企微**完全不可达**。需要把企微接入**站点同款 Agent 管线**，让企微用户获得与站点一致的 Agent 问答体验。

这不是"接检索"，而是**通道适配 + 输出投影**：Agent 是否检索、是否联网、是否澄清，全部由 Agent 自主决策（与站点一致）；本 change 只负责把入站消息喂进那条管线、并把管线的富输出投影进企微能渲染的子集。

## What Changes

- **通道 → Agent 桥接**：入站消息推导 `session_id`（群聊按 `chatid`、单聊按 `from_userid`）→ `msgid` 去重 → 生成 `trace_id` → 调用与站点同一条生成管线（`agent_service`）。
- **长连接驱动可靠性补丁**：认证等待（当前 `connect()` 不等认证即返回）、被顶号即停重连（防互踢）、长流 4min keepalive（防 6min 超时）。
- **企微流式输出投影**：把 Agent 的 SSE 事件流投影为 `reply_stream`——**累积全量快照**语义、节流发送、帧数与长度上限；引用降级为文末「参考来源」；`reasoning`/`delegate`/`task`/`model_info` 等无渲染通道的事件丢弃。
- **澄清回填**：`ask_user` 挂起期间在企微侧呈现问题，用户回复后回填 `pending_asks`（本轮以文本回填为主；卡片仅在有选项时可选，留后续）。
- **trace_id 三路返回**：`feedback.id`（首帧设置，供用户反馈回传）+ 日志锚点 + 可见 footer。
- **编排下沉（不改站点行为）**：把 `src/api/chat.py` 的"跑一轮 + 落库 + 终态"编排提取到 `services/`，供站点与通道共用（层间规则：`channels` 不得 import `api`）。

## Capabilities

### New Capabilities
- `wecom-agent-bridge`: 通道侧桥接——入站消息推导会话与身份、`msgid` 去重、`trace_id` 生成与三路返回、把消息喂进站点同款 Agent 管线、澄清答案回填、以及为支撑上述能力所需的驱动可靠性（认证等待 / 被踢停重连 / 长流保活）。
- `wecom-stream-projection`: 把 Agent 事件流投影为企微流式帧——快照式累积、节流、帧数/长度上限、引用降级、无通道事件丢弃、终态收尾与 trace_id footer。

### Modified Capabilities
<!-- 无。本 change 不改动现有主规格的 REQUIREMENT：编排下沉以"站点行为不变"为约束；wecom 通道规格由未归档的 wecom-multi-bot 引入，本 change 以新能力承载，不构成对现有主规格的修改。 -->

## Impact

- **代码**：
  - `src/channels/wecom/`：新增 `handler.py` / `presenter.py` / `dedup.py`；改 `long_connection.py`（认证/被踢/keepalive）。
  - `src/channels/base.py`：`ChannelDriver` 增加 `capabilities` 声明位。
  - `src/services/`：`wecom_service.py` 的 handler 由占位换成桥接 handler；新增共用编排（自 `api/chat.py` 下沉）。
  - `src/api/chat.py`：编排改为调用下沉后的 service 函数（外部行为不变）。
- **前置依赖**：`wecom-multi-bot` 已归档（`openspec/changes/archive/2026-10-07-wecom-multi-bot/`），其能力已提升为主规格 `openspec/specs/wecom-channel/spec.md`（多机器人装配/降级/关停与入站分发）；本 change 建立在其上。驱动侧的"认证时序/被顶停重连/长流保活"是 `wecom-channel` 未覆盖的**新**要求，故由本 change 的新能力 `wecom-agent-bridge` 承载，不改动该主规格。
- **依赖行为**：官方 `aibot` SDK（`WSClient.reply_stream` / `feedback` / 事件订阅）；其快照语义、认证时机、被踢行为须经 Spike 实证（见 design 与 tasks 组 0）。
- **文档**：`docs/agents/defensive-patterns.md`（SDK 补丁与投影层防复发）、`docs/agents/code-map.md`（通道层落点）。
- **风险**：编排下沉触及站点生成路径，须以"站点行为不变"为准绳并跑回归（`tests/` + 前端 SSE 冒烟）。
