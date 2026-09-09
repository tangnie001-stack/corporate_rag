# Design: session-process-replay

## Context

实时路径的 SSE 事件由前端现场聚合（Think 块、委派区、状态行），服务端不累积过程数据；历史回放（D7）只展示终稿。本对话中通过帧级核对（cookbook「调试」分区流程，回放 `trace_3157b559` 真实 447 帧：status×7 + token×431 + citation×7）确认了三个事实：① 中间轮存在 token（模型在工具调用前流式输出旁白）；② 旁白被前端无差别粘进正文气泡；③ 深度思考关闭是旁白出现的条件（官方文档：思考开启时工具调用前必有 `reasoning_content`）。设计稿 `docs/design/chat-eventstream-mockup-2026-09-08.html`（v2）为本实现的验收基准。

## Goals / Non-Goals

**Goals**
- 过程叙事持久化：每条 AI 回答保存完整有序事件（状态/思考/旁白/委派），刷新/切会话/隔天打开与实时观看一致
- 双路径渲染同构：历史回放与实时路径共用同一套前端聚合管线
- 修复旁白粘正文（实时）与 reasoning_content 不回传（深度思考开启时）

**Non-Goals**
- 不做 Redis 轨迹存储 / trace 查询端点 / 帧缓冲改造（帧级核对走日志 + 既有回放接口，cookbook 已登记）
- 不做服务端事件聚合状态机（见决策 D1）
- 不回填存量消息（process 为 NULL，前端判空跳过）
- 不改 sessions/list

## Decisions

### D1: 存「原始事件序」，不做服务端聚合（用户定案，替代初版 ProcessRecorder）

初版设计为服务端聚合状态机（块开关/轮次缓冲/旁白判定），被否决：聚合逻辑将存在 Python + JS 两份镜像需永久同步。改为：

- 生成中：`_StreamCapture` 新增 `events_log` 列表，事件循环里每转换出一个 SSE 事件即 append（type + payload_for_buffer()）
- 终态：`json.dumps` 为 `[{seq, type, payload}, ...]` 写入 process 列
- 聚合全部在渲染层：前端实时 handler 与历史回放共用同一套逻辑，**一套实现两个入口**

备选对比：服务端聚合（否，镜像维护缺陷源）/ 前端回报（否，客户端数据不可信且刷新即丢）/ 日志解析（否，日志无帧内容）。

**存储边界**：events_log 是私有列表，**不经过** StreamingRunManager 缓冲（其 MAX_ITEMS=2000 会截断头部、TTL 300s 会清空）——长回答 token 帧可达数千，截断会破坏事件完整性。体量实测估算：447 帧样本 ≈ 15KB；深度思考开启 ≈ 50-150KB/条，MEDIUMTEXT（16MB）无压力。

**结构版本化**：process 列存外层 wrapper 而非裸数组——`{"format_version": 1, "events": [{seq, type, payload}, ...]}`。payload 内部结构会随版本演进（如 status 增加字段），读取端按 format_version 分派解析方式，不认识则明确报错而非猜测（借鉴 deepseek-harness 存储契约的版本门禁思路）。

**序列化分拣**：events_log 中的 token 帧按轮次语义分拣——tool_calls 收尾轮的 content token 帧保留（旁白）；末轮 answer 的 token 帧**剔除**（answer 列已承载正文，保留会导致历史回放正文重复渲染）。排除类型共 5 类，均不入 process——收尾补发的 model_info/abstention/done（循环结束后由 capture 补发，非循环内可见元素；模型名/拒答语义/终态分别由 model_name 列、answer 措辞+消息 status、终态事件自身承载），以及 citation（sources 列承载，回放走 attachHistoryCitations，重复渲染会双份）与 error（interrupted 标签承载）。

**采集点双路统一（关键）**：事件到达有两条路径——主循环（astream_events → _convert_event，retrieve/status/token 帧）与并行 clarify drain（clarify_channel → _convert_event，delegate/ask_user 帧）。采集点 MUST 覆盖两路：两处调用点共用一个 `_record_event(capture, event)` helper，否则 delegate/ask_user 帧全部漏采、回放时委派区消失。events_log 的引用随采集起始即挂到 `partial_holder["events_log"]`（共享列表引用，取消路径也持续可见）；模型名同理在生成收尾写入 `partial_holder["model_name"]`——收尾落库点（chat.py `_run_with_finalize`，拿不到 capture 局部变量）经 partial_holder 读取。

### D2: 旁白判定——以「轮次收尾方式」为界（确定性规则）

一轮 = 一次模型调用。轮结束时：出了 tool_calls → 本轮 content 流为旁白（preamble）；未出 tool_calls（自然收尾）→ 本轮 content 流为正式回答（不进 process，走 answer 列）。依据：① 官方文档（思考开启时工具调用前必有 reasoning_content）；② `trace_3157b559` 447 帧实测（两段旁白均以 tool_calls 收尾，末轮 content 即正文）。前端实时路径用等价的**待定区机制**实现同一判定（流式时不知道本轮是否出工具调用）：token 进 pending 缓冲，出现 tool 调用 → 固化为旁白块；done 且从未有工具调用 → pending 即正文。备选：提示词抑制（否，采样行为不可根除，仅辅助）。

### D3: 相邻同型合并放渲染层，process JSON 保持逐元素粒度

规则：只合并「相邻且同类型」元素为一个组 div，类型不同即分拆，整体严格按到达顺序（只看相邻，不看全局）。粒度选择：JSON 存逐元素（seq 忠实），合并发生在渲染——实时（新 status 到达时末尾是 status 组则追加）与历史（遍历数组归组）同一规则。好处：将来调整合并规则，历史数据重放即得新视图，无需数据迁移。

### D4: reasoning_content 回传（必做）

实证：容器内 `langchain_openai/chat_models/base.py` docstring 明确 "provider-specific fields (`reasoning_content`…) are **not** extracted"，AIMessage 序列化只输出 role/content/tool_calls——思考链每轮被截断。官方要求多轮工具调用回传 reasoning_content（省略降低工具调用准确性），qwen3.7-flash 在 `preserve_thinking` 支持列表。实现：ChatOpenAI 子类覆写 AIMessage 序列化（additional_kwargs.reasoning_content → 请求 payload）+ 请求 extra_body 带 `preserve_thinking=true`；fork 用的 qwen3.8-max 默认开启无需设置。生效范围：仅深度思考开启时有实际内容。

### D5: 落库时机与取消路径

序列化写库发生在收尾落库（save_assistant_message 同一时刻），非每帧 INSERT。取消/超时路径：events_log 已含到中断点为止的全部事件，随 partial 落库（与既有 interrupted 语义一致）。不用 shield/线程兜底——events_log 是内存 append（不可被取消打断），终态后的 dumps 是纯内存操作。

### D6: 历史回放的复用边界与交互事件静态化

「历史回放复用实时渲染管线」的边界必须收窄为：**复用过程元素渲染函数**（status 组/Think 块/旁白块/委派区的 DOM 构建与相邻合并），**不复用整个 handler 集合**——实时 handler 含运行期副作用（done 会 finalizeAnswer + loadSessions() 刷侧栏 + 状态机切换；ask_user 会 renderComposer 接管输入区），历史回放触发这些会产生死卡片和错误收尾。对应规则：

- `ask_user` 帧在历史回放中渲染为**静态注记**（如"已向用户澄清"），不是交互卡片——澄清的回答本身是消息流中的独立 user 消息，天然在旁
- `error`/`done` 帧在历史回放中跳过（中断语义由消息 status=interrupted 标签承载，引用由既有 attachHistoryCitations 承载）
- ask_user 澄清暂停时**不复位** processContainer 指针——澄清后继续生成的帧追加进原容器，实时 DOM 结构与历史回放的单容器结构保持一致
- 测试样本固化：`trace_3157b559` 真实帧序裁剪后存为 `tests/fixtures/process_frames_sample.json`，作为 D1 单测与前端回放测试的共用数据源（帧缓冲 TTL 5 分钟，不固化则写测试时数据已不可得）

## Risks / Trade-offs

- [历史消息体量增大（深度思考开启时单条 50-150KB JSON）] → MEDIUMTEXT 上限 16MB 充裕；后续如 messages 响应变重，可加 `?with_process` 开关或前端懒加载（本期不做，YAGNI）
- [原始事件含全部 token 帧，JSON 噪声大] → 忠实性优先；delta 本身小（~10 字节/帧），聚合在渲染层零成本
- [verify 重生成轮：被拒回答的 token 流固化为旁白块，与实时气泡展示存在偏差] → 接受（Q4 决策 A）：低频场景（citation_guide 触发约 6 天 1 次）+ 过程真实性优先——被拒的尝试也是过程的一部分，且该文本在实时时同样向用户展示过；规则自然结果零额外代码
- [preserve_thinking 开启后 reasoning_content 会计入输入 token 计费] → 官方计费规则，深度思考场景属预期成本；仅思考开启时生效
- [存量消息 process=NULL] → 前端判空跳过重建，历史回放降级为现状（仅终稿），无报错
- [相邻合并规则与前端实时逻辑分叉] → 两路径共用同一 JS 函数（抽取公共函数），代码层强制同源
- [B1 待定区边界：纯闲聊（无工具调用）token 必须落正文] → 待定区在 done 时倒进气泡，规则已覆盖；用无工具调用的会话帧做测试用例

## Migration Plan

1. `ALTER TABLE conversation_history ADD COLUMN process MEDIUMTEXT NULL;`（手动执行，操作登记 cookbook；model_name 列已存在直接复用；可空列无需回填，先改代码后执行 DDL 或同发布窗口执行均可——代码对 NULL/缺列需先容忍）
2. 后端 → 前端 → 文档按 tasks 顺序落地，`docker compose restart app` 生效（override 挂载 src/）
3. 回滚： revert 代码即可；process 列保留无害（旧版前端不读取）
