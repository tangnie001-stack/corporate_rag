# Tasks: session-process-replay

依赖顺序：1 → 2 → 3 → 4 → 5 → 6 → 7；第 2 组（reasoning 回传）与 3-5 组相互独立可并行。

## 1. 表结构与采集层

- [ ] 1.1 `MessageModel` 增加 `process`（MEDIUMTEXT NULL）字段定义（模型名复用既有 `model_name` 列）；编写并登记 ALTER TABLE 语句
- [ ] 1.2 统一采集点：新增 `_record_event(capture, event)` helper，在**两条路径**的 `_convert_event` 之后调用——主循环（astream_events）与 clarify drain 任务（delegate/ask_user 帧走此路，单点采集会漏采）；`_StreamCapture` 增加 `events_log` 列表字段（独立于 manager 缓冲，无截断上限），并在采集起始将列表引用挂到 `partial_holder["events_log"]`、收尾写入 `partial_holder["model_name"] = capture.model_used`（落库点经 partial_holder 读取）
- [ ] 1.3 TDD：事件采集单测——用固化样本 `tests/fixtures/process_frames_sample.json`（`trace_3157b559` 真实帧序裁剪：status×7+token×431+citation×7+model_info+done）做用例，断言 events_log 顺序、类型计数与完整性；**clarify 通道路径用例**（delegate/ask_user 帧被采集）；取消路径断言采集中断点

## 2. reasoning_content 回传（独立，可与 3-5 并行）

- [ ] 2.1 TDD：序列化测试——构造含 additional_kwargs.reasoning_content 的 AIMessage，断言自定义序列化后请求 payload 携带该字段且含 preserve_thinking=true
- [ ] 2.2 实现 ChatOpenAI 子类（覆写 AIMessage 序列化），model gateway 装配处替换；请求 extra_body 增加 preserve_thinking=true（qwen3.7-flash）；fork 的 qwen3.8-max 不需额外处理（默认开启）

## 3. 落库与接口

- [ ] 3.1 落库链四层透传 `process_json` / `model_name`：`save_assistant_message` 与 `chat_repo.save_message`（白名单加 process）写入；chat.py 收尾调用点将 `json.dumps({"format_version": 1, "events": 分拣后事件})` 与 `capture.model_used` 传入。**分拣规则**：tool_calls 收尾轮的 content token 帧保留（旁白）；末轮 answer 的 token 帧剔除（answer 列已承载）；model_info/abstention/done/error/citation 五类帧不入 process。取消/超时 partial 路径同样分拣到中断点为止
- [ ] 3.2 `MessageItem` 增加 `process`、`model_name` 字段（app_service.get_messages 行枚举补 process 透传）；sessions.py messages 端点返回（存量 NULL 正常返回）
- [ ] 3.3 TDD：落库与接口契约测试（mock mysql：新字段写入/读出、存量 NULL 兼容、partial 路径）

## 4. 前端实时路径（chat.html）

- [ ] 4.1 旁白待定区：工具调用首现前的 token 进 pending 缓冲；**固化触发信号包括任何工具调用**——retrieve_kb/search_web 的 on_tool_start 与 delegate start 帧（delegate_task 的 on_tool_start 不产 status 帧，勿漏）；固化后写入旁白块；done 且无工具调用 → pending 倒入气泡
- [ ] 4.2 相邻同型合并：status 到达时末尾元素为 status 组则追加、否则新建组 div
- [ ] 4.3 抽取「过程元素渲染」公共函数（实时与历史共用），合并规则单点实现；**复用边界=过程元素渲染函数**，不含实时 handler 的运行期副作用（done 收尾/loadSessions/composer 接管），ask_user 帧在回放中静态化（见 design D6）
- [ ] 4.4 ask_user 澄清暂停时不复位 processContainer 指针（澄清后帧继续进原容器，实时 DOM 与回放单容器结构一致）——调整 `removeStreamingState` 的复位范围
- [ ] 4.5 历史回放跳过 answer 段 token 帧（序列化已剔除，防御式再校验：回放遇 token 帧仅渲染旁白），断言正文不重复

## 5. 前端历史回放（D7 反转）

- [ ] 5.1 `loadSessionMessages` assistant 分支：`m.process` 非空且 `format_version` 可识别时，按 events 数组顺序喂给 4.3 公共渲染函数重建过程容器（ask_user 帧静态注记、error/done 帧跳过）；null 或版本不可识别跳过（存量兼容，仅终稿）
- [ ] 5.2 确认历史重建与实时聚合产物一致：同一套 Think/委派/旁白/合并逻辑无分叉

## 6. 验证

- [ ] 6.1 playwright：实时模拟 `trace_3157b559` 帧序（status 组 1+2+1+2+1 合并、旁白块×2、正文无旁白粘连且不重复），对照设计稿 `chat-eventstream-mockup-2026-09-08.html` v2
- [ ] 6.2 playwright：历史回放重建对照（mock messages 返回 process，断言过程容器逐块一致、**正文不重复**、ask_user 静态化；存量 NULL 消息降级正常）
- [ ] 6.3 端到端：真实提问一次 → 帧回放（cookbook 流程）→ messages 取回 process → 前端重建，三方一致性人工核对
- [ ] 6.4 标准门禁：pytest 全过 / ruff 无错误 / pyright 无新增 error / 无遗留调试代码

## 7. 文档登记

- [ ] 7.1 api_contract.md：messages 新字段（process 结构、model_used）+ reasoning 回传约束
- [ ] 7.2 glossary.md：新术语「旁白（preamble）」；D7 反转说明
- [ ] 7.3 cookbook.md：ALTER TABLE 操作记录（帧级核对条目已在）
- [ ] 7.4 chat-harness.md / chat-delegate-progress 等设计规格与实现对齐（D7 反转、相邻合并规则）
