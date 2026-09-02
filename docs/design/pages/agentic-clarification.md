# Agentic Clarification — 前端组件设计

对应 `docs/openspec/changes/agentic-clarification`（澄清工具化 + 深度思考开关）的前端改动。视觉基线遵循 `docs/design/MASTER.md`（Minimalism & Swiss、primary `#3B82F6` / accent `#F97316`、720px 布局、Lexend + Source Sans 3、150ms 过渡）。

## 组件清单

| 组件 | 状态 | 触发 | 说明 |
|------|------|------|------|
| 状态标签 status-tag | 已有 | `SSEStatusEvent` | 胶囊样式 + 蓝色呼吸圆点 |
| 追问卡片 composer | 已有 | `SSEAskUserEvent` | **输入区接管**，不进消息流 |
| abstention 转人工 | 已有 | `SSEAbstentionEvent` | amber 提示条 + 转人工按钮 |
| 答案反馈 feedback | 已有 | 消息完成后 | 👍/👎 SVG 按钮，选中 primary 高亮 |
| 模型信息 model_info | 已有 | `SSEModelInfoEvent` | 小字 muted，fallback 显示徽标 |
| 引用卡片 citation | 已有 | `SSECitationEvent` | 编号 badge + 来源 + snippet |
| 深度思考开关 thinking-toggle | 新增（chat-thinking-toggle） | 用户操作 | 输入区紧凑 chip，控制 `enable_thinking` |

## 组件规格

### 1. 状态标签 status-tag

- 胶囊样式：圆角 999px、`--surface` 底、1px `--border`、12px `--text-secondary`
- 蓝色呼吸圆点：6px、`--primary`、`pulse` 动画（opacity 1↔0.35，1.2s）
- 位置：消息流内，align-self flex-start
- 文案：`on_chat_model_start` → "正在思考..."；`on_tool_start(retrieve_kb)` → "正在检索相关文档..."；`on_tool_end` → "检索完成，正在分析..."

### 2. 追问卡片 composer（输入区接管）

- **不进入对话消息流**（对齐 dsh QuestionComposer）：触发时 `hideInputArea()` 隐藏输入框 + 发送按钮，表单出现在输入区；消息流只保留用户消息与状态标签
- 结构：卡片头（"需要补充信息" + SVG help 图标）→ 每问题一个 section（radio/checkbox 选项卡片 + 自定义输入）→ 底部提交按钮
- 卡片：`--surface` 底、12px 圆角、`--shadow-lg`、占满输入区
- 选项卡片：1px `--border`、10px 圆角、8px 12px 内边距；选中态 `--primary-light` 底 + primary 边框（`:has(input:checked)`）；hover 边框变 primary
- 自定义输入：虚线边框，focus 变实线 primary
- 提交按钮：primary 底白字、13px、hover `--primary-hover`；提交中 disabled
- 交互：提交走 `POST /clarify-answer`，**不关闭 SSE 连接**，同流续答；done 或断连时关闭 composer 恢复输入

### 3. abstention 转人工

- amber 提示条：`--amber-light`(#FFFBEB) 底、`#FDE68A` 边框、`#92400E` 文字、12px 圆角、信息图标（SVG）
- "转人工咨询"按钮：accent 橙 outline，hover 填充 accent 白字
- 位置：消息流内 AI 回答下方

### 4. 答案反馈 feedback

- AI 回答下 28×28px SVG 图标按钮（thumb up/down，**不用 emoji**）
- 未选中 `--text-muted`；hover `--primary-light` 底 + primary 图标；选中 `.active` 持色
- 交互：点击后 `POST /api/feedback`（rating "positive"/"negative" 字符串）

### 5. 模型信息 model_info

- 11px `--text-muted`："模型: xxx"
- fallback 时：amber 胶囊徽标（`--amber-light` 底、`#FDE68A` 边框、`#92400E` 文字）

### 6. 引用卡片 citation

> v3 交互（2026-09-02）：由「消息下方展开来源卡列表」→「末尾逐个胶囊 [n]」→「**末尾一体来源横条 + 右侧引用抽屉**」。整体形态：消息末尾一条整体按钮（不占纵向空间），点击从**屏幕右侧**滑出抽屉列出全部引用，**不遮挡中间对话**。页面级形态见 `chat-harness.md`「消息流 - 引用」。

- **SSE 数据**：`SSECitationEvent` 提供 `source`（文件名）/`page`（页码）/`snippet`（摘要）/`index`（序号）；前端按 index 顺序累计
- **正文内联引用号**：markdown 渲染后的 `[n]` 为可点击元素（12px primary 加粗，hover primary-light 圆角底），点击打开右侧抽屉并**高亮定位**到对应条目
- **末尾来源横条（一体按钮）**：AI 回复 markdown 之后**一条**整体按钮「来源 [1] [2] ›」；`--surface-muted` 底、1px `--border`、圆角 8px、padding 5×12px；hover 边变 primary 底变 `--primary-light`；**整条可点击**
- **右侧引用抽屉（drawer）**：fixed right 0、宽 380px（max 88vw）、左 1px `--border` 描边、阴影 `-8px 0 28px`；滑出动画 translateX 220ms ease；遮罩 `rgba(15,23,42,0.24)`；头部（文件夹图标 + 标题"引用来源" + ✕ 关闭）；列表区列出**全部**引用条目（左侧 [n] 编号方块，focus 时 primary 底白字；右侧文件名 + 「第N页」徽标 + 摘要）
- 点击外部遮罩 / Esc / ✕ 关闭；同屏单开
- 无引用（未命中 KB / 纯对话）时不渲染来源横条

### 7. 深度思考开关 thinking-toggle（新增）

- 输入区紧凑 chip 按钮（28px 高、圆角 24px、显示 "⚡ 深度思考"），点击切换，**无常驻大块 UI**
- 未选中：`--text-secondary`，hover 浅灰底；选中：primary 底白字 + ✓ 标记
- hover tooltip："开启后模型先思考再回答，更准确但响应更慢"
- 状态随 `/chat/stream` 请求以 `deep_thinking=true/false` 传递；后端 agent LLM `enable_thinking`（per-call extra_body）；默认 false
- 思考过程不展示（`reasoning_content` 被 langchain 丢弃）

## 验证

改完用 `playwright-cli` 打开 `http://localhost/` 验证：澄清 composer 交互、转人工入口、反馈按钮、深度思考开关切换。
