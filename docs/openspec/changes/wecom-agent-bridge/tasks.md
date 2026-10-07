## 0. Spike：官方 aibot SDK 行为事实表（实施前置，人工确认）

- [ ] 0.1 前置：先关线上 wecom（`WECOM_BOT_ENABLED=false` + 重建容器），本地 `.env` 只放一台机器人（单行），避免与线上连接互踢
- [ ] 0.2 写临时探针脚本 `scripts/spike/wecom_sdk_probe.py`（`WSClient` + `on(...)` 打印原始 body，CLI 选实验号；跑完即删）
- [ ] 0.3 E1 快照语义：同 stream 发 `甲`→`甲乙`→`甲乙丙`→`甲乙丙`(final)，人工看气泡最终内容，判定"整段替换 or 追加"
- [ ] 0.4 E2 重复/空白帧：插"内容未变"帧与空白/零宽帧，观测能否安全跳过、是否产生空气泡
- [ ] 0.5 E3 认证等待：正常 connect 记录 `connect()` 返回时刻 vs `authenticated` 事件；错 secret connect 观测是否抛/是否仅 `on_error`/是否静默假连
- [ ] 0.6 E4 被顶号：同 bot 先后建 A/B 两条连接，观测 A 的 `disconnected`/`reconnecting` 序列与是否无限互踢
- [ ] 0.7 E5 卡片回调结构：发 `button_interaction` 卡，人工点按钮，抓 `template_card_event` body，记录 key 字段名与 userid/chatid 回带
- [ ] 0.8 E6 帧节奏与 ack：连发 30 帧，记录每帧耗时与是否出现 5s `Reply ack timeout`
- [ ] 0.9 E7 长流保活：开流并每 4min 发 `finish=false`，跨越约 7min，验证 6min 时限与保活有效性
- [ ] 0.10 E8 Markdown 渲染：发标题/列表/代码块/表格/链接，记录哪些语法被渲染（定"参考来源"排版）
- [ ] 0.11 E10 trace_id 闭环：首帧带 `feedback={"id":"trace_test"}`，人工点赞，抓 `feedback_event` body；并确认终态 footer 的渲染观感
- [ ] 0.12 产出「官方 aibot SDK 行为事实表」（结论+证据+对设计影响），**人工确认 blocker E1/E3/E4/E5**（确认点 A）；据结论锁定 D5（累积/增量）与 D9（feedback 承载）的实现分叉

## 1. 前置与结构

- [ ] 1.1 确认前置已就位：`wecom-multi-bot` 已归档、主规格 `wecom-channel` 已生成，长连接驱动与会话分发可用
- [ ] 1.2 编排下沉：把 `src/api/chat.py` 的"跑一轮 + 落库 + 终态"提取为 `services/` 函数，站点路径改为调用它（外部行为不变）
- [ ] 1.3 回归：`POSTGRES_HOST=localhost pytest tests/` 全绿 + 前端 SSE 冒烟，确认站点行为未变

## 2. 通道抽象与配置

- [ ] 2.1 `ChannelDriver` 增加声明式 `capabilities`（stream / markdown 等），业务按能力位降级
- [ ] 2.2 新增可配置项：footer 开关、节流间隔、中间帧上限、单流长度上限（入 `settings` / `const`，不散落）

## 3. 桥接 handler

- [ ] 3.1 `session_id` 推导：群聊 `wecom:{bot}:group:{chatid}`、单聊 `wecom:{bot}:single:{from_userid}`
- [ ] 3.2 `msgid` 去重模块（TTL + 上限双淘汰）+ 单测（重复丢弃、有界淘汰）
- [ ] 3.3 每轮生成生成 `trace_<uuid>` 并 set `current_trace_id`；日志锚点同时带 `trace_id`/`bot_key`/`msgid`
- [ ] 3.4 `RagChannelHandler`：调用下沉后的 service 编排、订阅事件、交给投影层（`kb_id` 本轮传空）
- [ ] 3.5 同会话并发冲突处理：会话被占用时回"正在处理上一条"，不静默丢
- [ ] 3.6 `wecom_service` 将占位 handler 换成 `RagChannelHandler`

## 4. 输出投影层

- [ ] 4.1 `WeComPresenter` 三段式骨架（`start` / `update` / `finalize`，共享节流）
- [ ] 4.2 快照累积发送 + 节流（间隔下限）+ 内容未变/空白/零宽帧跳过
- [ ] 4.3 中间帧数与单流内容长度上限（超限保留尾部，不中断）
- [ ] 4.4 引用来源收集为文末「参考来源」（随终态帧发送；无引用不出现）
- [ ] 4.5 无渲染事件（reasoning/delegate/task/model_info/agent_used）丢弃且不中断
- [ ] 4.6 终态收尾：`finish=true`、正文尾部附 `trace_id`、错误以文本提示
- [ ] 4.7 首帧携带 `feedback.id = trace_id`（按 E10 结论定字段）
- [ ] 4.8 流式发送失败时降级为一次性非流式文本回复
- [ ] 4.9 投影层单测（假 sink，覆盖 4.2–4.8 各规范场景）

## 5. 驱动可靠性补丁

- [ ] 5.1 认证等待：`start()` 等待认证结果，成功才算就绪；失败可见且不计入成功（按 E3 结论实现）
- [ ] 5.2 被顶号停止重连（按 E4 结论实现），避免互踢
- [ ] 5.3 长流保活：流式期间 ≤4min 发一次非终态帧（按 E7 结论定参数）

## 6. 澄清回填

- [ ] 6.1 会话存在挂起 `ask_user` 时，入站文本作为答案回填 `pending_asks`（不重开回合）；无挂起时按普通消息处理
- [ ] 6.2 `ask_user` 事件在企微侧呈现为文本问题（dash 模式以纯文本问题为主）
- [ ] 6.3 澄清回填单测（有挂起 / 无挂起 / 超时）

## 7. 验证与文档

- [ ] 7.1 单台灰度 E2E：@ 该机器人给出站点同款 Agent 回答（**确认点 B**）
- [ ] 7.2 鲁棒验收：长流不断、重复 msgid 不重复、断线可恢复、引用/来源正确（**确认点 C**）
- [ ] 7.3 群聊验收：群内多人共享上下文、并发冲突提示正确（**确认点 D**）
- [ ] 7.4 登记 `docs/agents/defensive-patterns.md`（SDK 补丁与投影层防复发）与 `docs/agents/code-map.md`（通道层落点）
- [ ] 7.5 收尾：恢复线上 wecom 开关，更新 `deploy-runbook` 相关说明
