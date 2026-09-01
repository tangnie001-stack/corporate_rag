# agent-harness-foundation Tasks

## 1. CLAUDE.md 认知层（文档）

- [ ] 1.1 标题 `# Corporate RAG` 改为 `# Corporate Agent Harness`，说明定位为聊天 harness + 可插拔能力
- [ ] 1.2 角色描述改为"负责企业智能助手 harness（聊天底座 + 可插拔工具/知识库）的设计；RAG 知识库是其中一个能力模块"
- [ ] 1.3 技术栈补充 MCP（能力加载标准）
- [ ] 1.4 `docs/agents/reference-projects.md`：harness 类参考项目（claude-code / deepseek-harness / codex）的"何时查阅"优先级前置

## 2. 时间结构化约束（spec: temporal-constraint）

- [ ] 2.1 候选年份派生：新增模块读取绑定 KB 文档 `meta_info` 聚合 year / report_period 实体，生成候选年份集合（含季度粒度）
- [ ] 2.2 时间解析器：**时间词正则粗筛（`近|这|上|今|去|几|最近|前几年`）命中才调用** LLM 结合今日日期与候选集合输出结构化 JSON（years），只能从候选选择；代码校验合法性；未命中/失败/无候选走"无时间约束"默认路径（零调用）
- [ ] 2.3 `RequestContext` 扩展：新增 `temporal_years` / `missing_years` / `web_confirmed` 字段（供验证循环读取/会话内记住联网确认）
- [ ] 2.4 `retrieve_kb` 工具接入：调用前执行时间解析；缺失年份写入 `RequestContext.missing_years`（作为询问用户是否联网的依据，不注入给 LLM 自主搜索）
- [ ] 2.6 时间约束配置开关（env，如 `TEMPORAL_PARSE_ENABLED`）：关闭时跳过解析走默认路径，出错可即时关闭
- [ ] 2.5 单测：候选派生（KB 元数据聚合）、解析（"这几年"→[2023,2024,2025]）、越界拒绝、缺失年份判定（KB 仅 2024 → missing [2023,2025]）、正则粗筛命中/未命中、开关禁用时直接跳过

## 3. 验证循环（spec: answer-verification）

- [ ] 3.1 完整性校验函数：**正则提取答案年份（`\d{4}`，来自 `AgentState.answer`）**，比对"要求覆盖年份（RequestContext.temporal_years）"，输出缺失清单
- [ ] 3.2 忠实度校验：LLM judge（**复用 `RAGAS_LLM_MODEL`，temperature=0**）对照引用上下文逐句核对答案事实点（Self-RAG IsSup 思路），输出无支撑句子清单；judge 只标记不删内容
- [ ] 3.3 `workflow.py` 插入 `verify` 节点：`agent_finalize` → `verify` → 条件边（通过→`format`）；**缺失年份时经 `ask_user` 机制（复用 clarify_channel）询问"是否联网补充"** → 用户确认才触发 search_web 补充并回 agent 重生成，拒绝则标注"知识库仅覆盖 X 年"后直接 format
- [ ] 3.4 会话内记住联网确认：`RequestContext.web_confirmed` 置位后，后续缺失年份直接联网不再询问；修订终止条件：LLM judge 最多 2 轮，超限转拒答（标注信息不足）或转人工；新增配置开关（env）即时启停
- [ ] 3.5 单测：完整性缺失触发询问、用户确认/拒绝分支、web_confirmed 会话内不重复询问、无支撑断言标记、轮次上限转拒答、开关禁用时直接通过
- [ ] 3.6 验证循环日志：校验结果（通过/不通过/轮次/缺失年份清单/询问与确认结果）打点，便于排查

## 4. 工具注册表化（spec: tool-registry）

- [ ] 4.1 实现 `ToolRegistry`：注册/注销/启停/依赖注入/返回启用工具列表
- [ ] 4.2 `make_rag_tools` 改造：向注册表注册 retrieve_kb / ask_user / search_web 后取列表，行为不变
- [ ] 4.3 工具按粒度启停：泛化 `WEB_SEARCH_ENABLED` 语义为任意工具开关（env / 注册表配置）
- [ ] 4.4 **KB=RAG 开关**：会话未绑定 KB 时不注册 retrieve_kb（工具不出现在 LLM 可见列表）；`kb_id=""` 语义改为"不检索"（`kb_router_node` 空值时 `_resolved_kb_ids=[]`），废弃隐式跨库
- [ ] 4.5 MCP 适配器入口预留：统一工具 schema 定义，MCP 工具可经适配器注册（本 change 不实际接 MCP server）
- [ ] 4.6 单测：注册/启停/返回列表、新工具注册不影响主循环、停用工具不出现在 LLM 可见列表、空 kb_id 不检索（retrieve_kb 返回空）

## 5. 聊天页 UI 重构（spec: chat-harness-ui，设计稿 docs/design/pages/chat-harness.md）

- [ ] 5.1 侧栏重构：头部（Logo+品牌+版本号）/ 功能块（新建会话+知识库管理）/ 会话历史列表 / 底部登录头像，整体浅色系
- [ ] 5.2 双页面形态：新对话页（居中品牌+标题+居中输入框，无示例问题）/ 历史对话页（顶栏+消息流+底部输入框）；侧栏切换逻辑
- [ ] 5.3 知识库选择器：新对话页输入框上方独立行，单选弹窗（列出 KB + 知识库管理入口），默认"选择知识库"/勾选显示 KB 名/取消显示"请选择知识库"
- [ ] 5.4 KB 会话级绑定：新建会话选定后不可改；历史对话页顶栏显示 `会话名 知识库:XXX`（未绑定时只显示会话名）
- [ ] 5.5 消息流：用户浅灰气泡（#F1F5F9）/ AI markdown 回复 + 模型标注（由 {model} 回答）/ 去时间戳 / 保留引用卡与工具状态
- [ ] 5.6 **前端实现走 `frontend-design` skill**：按 `docs/design/pages/chat-harness.md` 与 mockup 落地到 `deploy/nginx/html/chat.html`（视觉/排版/交互）
- [ ] 5.7 playwright 验证：打开 chat.html 对照设计稿（布局/KB 选择器交互/消息流），无 console 错误

## 6. 质量门禁与验证

- [ ] 6.1 `pytest tests/ -v` 全量通过；`ruff check .` 无错误；`pyright src/` 不引入新 error
- [ ] 6.2 手动验证："腾讯这几年业绩怎么样"（绑定腾讯 KB）→ 解析出 [2023,2024,2025]、缺失 [2023,2025]、询问"是否联网"→ 确认后补充、拒绝则标注"知识库仅覆盖 2024 年"；未绑定 KB 会话不调用 RAG
- [ ] 6.3 契约同步：**更新 `docs/agents/api_contract.md` 与 `docs/agents/glossary.md` 中 `kb_id` 空串语义**（"搜索所有知识库"→"不检索"）；改 API/公共方法签名或响应结构时同步更新契约与受影响测试断言
