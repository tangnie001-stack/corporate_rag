# chat-markdown-rendering

## Why

前端聊天界面把 LLM 输出当**纯文本**平铺显示。`deploy/nginx/html/chat.html` 的 `renderAiAnswerStream` 用 `document.createTextNode` 追加 token，从未引入 markdown 渲染库。而 LLM 输出是结构化 Markdown（`###` 标题 / `**` 加粗 / `*` 列表 / 代码块），在界面上以原始语法平铺显示，观感差。

`web-search-fallback` 上线后问题加剧：web 兜底答案是长文结构化 Markdown，平铺显示非常明显（实测 trace_4a252403 的答案含 `###`、`**`、`*` 但界面显示为字面文本）。

对照参考项目 deepseek-harness：其聊天 UI 用 `packages/client/ui-primitives/src/markdown/MarkdownText.tsx`——基于 micromark（GFM + 数学扩展）的"未信任内容 Markdown 渲染器"，**流式增量解析**（流式时只重解析尾部、前面块冻结为缓存），支持代码块/脚注/数学公式，并做 XSS 安全处理。

本 change 让前端正确渲染 LLM 的 Markdown 输出，对齐 deepseek-harness 的渲染能力。

## What Changes

### 1. 前端 Markdown 渲染（核心）

- `chat.html` 的助手消息气泡改为 **Markdown 渲染**，替换纯文本 `createTextNode`
- 渲染库选择（按项目约束：nginx 静态托管、无 node 构建，需 CDN 或 vendor 本地化）：
  - 轻量方案：`marked` 或 `markdown-it`（单文件、易 vendor、够用）
  - 对齐 dsh 方案：`micromark` + 增量解析（流式性能最优，但引入多个依赖文件，复杂度高）
- **流式渲染策略**（参考 dsh `MarkdownText` 的"冻结块 + 尾部重解析"）：
  - 流式进行中：已稳定的部分渲染为 markdown，尾部未闭合语法做容错（`marked`/`markdown-it` 对未闭合语法天然容错；`micromark` 则用增量解析）
  - 简化可行方案：流式时按纯文本实时追加，**结束后整段 markdown 渲染**（体验略降但实现简单，作为备选）
- **XSS 安全**：LLM 输出是未信任内容，渲染必须 sanitize——`marked`/`markdown-it` 需配 DOMPurify 或白名单过滤；dsh 方案内置 sanitize URI
- **引用展示兼容**：现有 `[n]` 引用标记与 citation 事件保持不变，Markdown 渲染不能破坏引用编号文本

### 2. 样式（走 UI 设计流程）

- 按 `docs/agents/ui-design-flow.md` 走 `ui-ux-pro-max`：
  - 页面规格 `docs/design/pages/chat-markdown.md`（中文：markdown 元素视觉规格——标题层级/代码块/列表/引用块/表格/行内代码/加粗）
  - 效果预览 `docs/design/chat-markdown-mockup.html`（独立 HTML，playwright 验证）
- 新增 markdown 元素 CSS（代码块背景/等宽字体、标题字号层级、列表缩进等），对齐 `docs/design/MASTER.md` 全局设计系统

### 3. 渲染范围界定

- 仅**助手消息**渲染 markdown；用户消息、系统消息、澄清表单保持纯文本/现有渲染（防注入）
- 历史会话消息回放时同样走 markdown 渲染

## Capabilities

### New Capabilities

- `chat-markdown-rendering`: 助手消息 Markdown 渲染——流式容错、XSS 安全、markdown 元素样式，对齐 deepseek-harness 渲染能力

### Modified Capabilities

（无。前端 chat.html 无对应 main spec。）

## Impact

- **修改文件**：
  - `deploy/nginx/html/chat.html` — `renderAiAnswerStream` 改 markdown 渲染；新增 markdown 元素 CSS；引入渲染库（CDN 或 vendor 本地 JS）
  - `docs/design/pages/chat-markdown.md`（新建）— 页面规格（ui-ux-pro-max 产出）
  - `docs/design/chat-markdown-mockup.html`（新建）— 效果预览
- **依赖**：前端引入 markdown 渲染库（`marked` / `markdown-it` / `micromark` 选一）；因 nginx 静态托管无构建链，需 vendor 本地化或 CDN 引入
- **参考实现**：deepseek-harness `packages/client/ui-primitives/src/markdown/MarkdownText.tsx`（micromark + 增量流式 + sanitize）
- **不做（后置）**：代码高亮（prism/highlight.js，可后续）、LaTeX 数学公式（dsh 有，非必需）、web 引用缺失问题（独立于本 change，另议）
- **验证**：playwright-cli 验证 mockup 与真实对话渲染
