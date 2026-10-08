## 0. Spike：官方 aibot SDK 行为事实表（实施前置，人工确认）

- [x] 0.1 前置：**改 `docker-compose.image.yml` 的 `environment`（或加 override）关掉线上 wecom 再重建** —— `.env` 被 compose `environment` 覆盖，置 `.env` 无效；本地 `.env` 只放一台机器人（单行），避免与线上连接互踢
- [x] 0.2 写临时探针脚本 `scripts/spike/wecom_sdk_probe.py`（`WSClient` + `on(...)` 打印原始 body，CLI 选实验号；跑完即删）
- [x] 0.3 E1 快照语义：同 stream 发 `甲`→`甲乙`→`甲乙丙`→`甲乙丙`(final)，人工看气泡最终内容，判定"整段替换 or 追加"
- [x] 0.4 E2 重复/空白帧：插"内容未变"帧与空白/零宽帧，观测能否安全跳过、是否产生空气泡
- [x] 0.5 E3 认证等待：正常 connect 记录 `connect()` 返回时刻 vs `authenticated` 事件；错 secret connect 观测是否抛/是否仅 `on_error`/是否静默假连/是否持续重连
- [x] 0.6 E4 被顶号：同 bot 先后建 A/B 两条连接，观测 A 的 `disconnected`/`reconnecting` 序列与是否无限互踢
- [x] 0.7 E5 卡片回调结构：发 `button_interaction` 卡，人工点按钮，抓 `template_card_event` body，记录 key 字段名与 userid/chatid 回带（仅影响后续卡片）
- [x] 0.8 E6 帧节奏与 ack：连发 30 帧，记录每帧耗时与是否出现 5s `Reply ack timeout`
- [x] 0.9 E7 长流保活：开流并每 4min 发 `finish=false`，跨越约 7min，验证 6min 时限与保活有效性
- [x] 0.10 E8 Markdown 渲染：发标题/列表/代码块/表格/链接，记录哪些语法被渲染（定"参考来源"排版）
- [x] 0.11 E10 trace_id 闭环：首帧带 `feedback={"id":"trace_test"}`，人工点赞，抓 `feedback_event` body（记录承载字段、`feedback.id` 约束、**该帧是否含 `msgid`**、**是否含 `msgtype`**、**SDK 是否 emit `event.feedback_event`**）；并确认终态 footer 的渲染观感
- [x] 0.12 E11 首帧前时限与占位首帧：测"长时间不发首帧是否被断"，以及先发占位帧能否有效规避
- [x] 0.13 产出「官方 aibot SDK 行为事实表」（结论+证据+对设计影响），**人工确认 blocker E1/E3/E4/E5**（确认点 A）；据结论锁定 D5（快照语义）与 D9（feedback 承载，**不可用则 footer 强制开启**）；**若 E1 反证为追加，须先修订投影 spec 的「快照式流式投影」需求再实施**

## 1. 前置与结构

- [ ] 1.1 确认前置已就位：`wecom-multi-bot` 已归档、主规格 `wecom-channel` 已生成，长连接驱动与会话分发可用
- [ ] 1.2 下沉入口：在 `services/` 实现 `start_turn(...)`（含 `set_chat_repo` 前置、原子闸门、**预留在多早退路径释放**、落库（可选 `title`）、生成、终态收尾），见 design D12
- [ ] 1.3 编排替换：`src/api/chat.py` 改为调用 `start_turn`（`TurnBusy` → 409）；`src/api/sessions.py` 的锁释放函数 import 从 `api.chat` 改为 services
- [ ] 1.4 依赖注入：`AppService` 单例访问器下沉到 `services/`，`api/dependencies` 变转发薄壳（design D20）
- [x] 1.5 下沉澄清答案应用：把 `api/clarify.py` 的"pop `pending_asks` + 格式化 + Redis/MySQL 双写"提取为 services 函数，站点与通道共用（design D14）
- [ ] 1.6 回归：`POSTGRES_HOST=localhost pytest tests/` 全绿 + 前端 SSE 冒烟，确认站点行为未变

## 2. 通道协议与配置

- [ ] 2.1 `ReplySink.reply_stream` 增加**可选** `feedback` 参数，同步 `callback.py` / `long_connection.py` 两实现与相关测试（不破坏既有用法）
- [ ] 2.2 新增可配置项：footer 开关、节流间隔、中间帧上限、单流长度上限、保活间隔（入 `settings` / `const`，不散落）

## 3. 桥接 handler

- [x] 3.1 `session_id` = `uuid5(WECOM_NS, "wecom|{bot_key}|{group|single}|{id}")`、`user_id` = `uuid5(WECOM_NS, "wecom-user|{from_userid}")`（均 36 字符稳定）；**`WECOM_NS` 为 `const.py` 中的固定字面常量**（不得随机/进程内），并加"跨进程稳定"单测；`bot_key`/`chatid`/`userid` 记入日志与 `sessions.title`
- [x] 3.2 `msgid` 去重模块（TTL + 上限双淘汰）+ 单测（重复丢弃、有界淘汰）
- [x] 3.3 每轮生成用既有 `new_trace_id()` 生成 `trace_<uuid>` 并 set `current_trace_id`；日志锚点同时带 `trace_id`/`bot_key`/`msgid`
- [x] 3.4 `RagChannelHandler`：调用 `start_turn`、订阅事件、交给投影层（`kb_id` 本轮传空）；`TurnBusy` → 回"正在处理上一条"
- [x] 3.5 同会话并发冲突：由 `start_turn` 的原子闸门保证，通道只翻译 `TurnBusy`，不静默丢
- [x] 3.6 `wecom_service` 将**长连接**的占位 handler 换成 `RagChannelHandler`；`callback` 模式保持占位不变
- [x] 3.7 澄清挂起时登记"会话 → 触发者 userid"映射（**TTL + 上限**），供回填时校验来源（见 6.1）
- [x] 3.8 消费 `event.feedback_event`：取回反馈标识并还原 `trace_id` 落日志（按 E10 结论定字段）
- [x] 3.9 ~~反馈回执解析分流~~ —— **Spike E10 实测后删除**：回执帧同时带 `msgid` 与 `msgtype`，不会在 SDK 门禁或 `parse_inbound` 被丢弃（见 `docs/agents/wecom-sdk-facts.md`）


> 阶段 3 已完成并并入 `dev-wsl`（merge `bffa728`）。**确认点 B（真实连网）通过**：@ `dev` 得到站点同款 Agent 回答（`[wecom] inbound bot_key=dev …` → 落库前置 → 任务注册 → prompt 组装 → model turn → format done，error 日志 0 行）；同轮实测并修复两处：投影期日志 trace 归属、投影期异常补发第二帧终态。

## 4. 输出投影层

- [ ] 4.1 `WeComPresenter` 三段式骨架（`start` / `update` / `finalize`，共享节流）
- [ ] 4.2 快照累积发送 + 节流（间隔下限）+ 内容未变/空白/零宽中间帧跳过
- [ ] 4.3 中间帧数与单流内容长度上限（超限保留尾部，不中断）
- [ ] 4.4 引用来源收集为文末「参考来源」（随终态帧发送；无引用不出现）
- [ ] 4.5 无渲染事件（reasoning/delegate/task/model_info/agent_used）丢弃且不中断
- [ ] 4.6 终态收尾：`finish=true`、正文尾部附 `trace_id`、出错以**脱敏文案**提示（原始异常只进日志，禁止透传 buffer 内的 error 文本）
- [ ] 4.7 首帧携带 `feedback.id = trace_id`（按 E10 结论定字段；**不可用则 footer 强制开启**）
- [ ] 4.8 流式发送失败时降级为一次性非流式文本回复
- [ ] 4.9 占位首帧：有首个 `status` 事件则以其渲染；长时间无内容则先发占位，不空等
- [ ] 4.10 长流保活：事件流以 **`asyncio.Queue`** 暴露（后台 pump 入队），投影对 `queue.get()` 施加超时；流式期间 ≤4min 发一次非终态帧（澄清期无事件也须保活；**保活帧豁免"内容未变跳过/空白不发送"，重发当前快照**）。**禁止**对上游在途取值施加超时取消（会终结生成器、静默截断流）；含 pump 生命周期与消费者提前退出时的回收；参数按 E7 结论定
- [ ] 4.11 空回答 / `abstention` 兜底：拒答追加转人工提示；终态正文为空则发兜底文案并结束（不留悬挂流）
- [ ] 4.12 投影层单测（假 sink，覆盖 4.2–4.11 各规范场景）

## 5. 驱动可靠性补丁

- [ ] 5.1 认证等待：`start()` 等待认证结果（**上界 ≤5s**），成功才算就绪；**凭证类失败**（信号仅限 SDK SUBSCRIBE 响应 `errcode≠0`——实测错误凭证为 **`853000`** / `errmsg=invalid bot_id or secret`；**不含普通 `on_error`**）→ 可见告警、**主动 `disconnect()` 并置停重连标志**、不计入成功；**等待超时**→ 只置降级（不计入成功）、**不断开不停重连**（交 SDK 自愈）；**未就绪驱动仍登记入 `_drivers` 供 `stop()` 关闭，锚点只计认证就绪者**；**逐台启动改为 `asyncio.gather(..., return_exceptions=True)` 并行**以免拖垮启动（按 E3 结论实现）
- [x] 5.2 被顶处置（**驱动部分已完成**，`eaa85ee` 经 merge 并入）：驱动**单独注册驱动级处理器**订阅 `event.disconnected_event`（刻意**不**放进 `_EVENT_EVENTS`——被顶是连接归属问题，不应污染业务 handler 的输入面）→ 记 warning + 主动 `disconnect()` 且不再抢回 + 暴露只读 `is_displaced`（幂等）；**就绪口径与启动锚点对该标志的消费仍待 5.1**

## 6. 澄清回填

- [x] 6.1 **仅触发澄清者**的入站文本回填 `pending_asks`（不重开回合）；他人消息按普通消息处理（会话正忙回"正在处理上一条"）
- [x] 6.2 `ask_user` 事件呈现为**一条编号问题**（多问合并），用户整段回答作为单个自定义答案回填（实际落地：**逐问编号 + 逐条映射**，**取代**原『合并为一问』）
- [x] 6.3 回填调用**下沉后的同一答案应用函数**（写 Redis 历史 + MySQL），不得复制站点逻辑（见 1.5）
- [x] 6.4 澄清回填单测（触发者回填 / 他人不误吞 / 无挂起走普通 / 多问合并 / 双写 / 超时）

> 4b 已完成并**并入 dev-wsl**（merge `17aee87`）；确认点 C（真实连网：@dev 触发澄清 → 气泡见问题 → 回复 → 同气泡出答案；群内非触发者插话不被当答案）**待验证**——三次尝试中模型均未调用 `ask_user`（工具描述"能回答就不要调用" + 会话历史 priming），故「澄清呈现 / 仅触发者注入 / 同气泡续跑」三段目前**仅单测覆盖**，尚无端到端实证。

## 7. 验证与文档

- [ ] 7.1 单台灰度 E2E：@ 该机器人给出站点同款 Agent 回答（**确认点 B**）
- [ ] 7.2 鲁棒验收：长流不断（含澄清等待期）、重复 msgid 不重复、断线可恢复、引用/来源正确、错误文案脱敏（**确认点 C**）
- [ ] 7.3 群聊验收：群内多人共享上下文、并发冲突提示正确、澄清仅触发者可回填（**确认点 D**）
- [ ] 7.4 登记 `docs/agents/defensive-patterns.md`（SDK 补丁与投影层防复发）与 `docs/agents/code-map.md`（通道层落点）
- [ ] 7.5 收尾：按 compose `environment`（或 override）恢复线上 wecom 开关（`.env` 无效），更新 `deploy-runbook` 相关说明
