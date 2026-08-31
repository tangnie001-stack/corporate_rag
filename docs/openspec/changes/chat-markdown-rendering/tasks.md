# chat-markdown-rendering Tasks

## 1. UI 设计稿（前置）

- [ ] 1.1 用 `ui-ux-pro-max` skill 生成页面规格：`docs/design/pages/chat-markdown.md`（中文，含 markdown 元素视觉规格——标题层级/代码块/列表/引用块/表格/行内代码/加粗，对齐 `docs/design/MASTER.md` 全局设计系统）
- [ ] 1.2 产出效果预览：`docs/design/chat-markdown-mockup.html`（独立 HTML，含典型助手 markdown 长文 + 代码块 + 表格样例）
- [ ] 1.3 用 playwright-cli 验证 mockup 视觉（标题/代码块/列表/表格渲染正常，不截图、提交 HTML）

## 2. 前端依赖引入

- [ ] 2.1 vendor 本地 JS 到 `deploy/nginx/html/vendor/`：`marked.min.js`（含完整 Markdown 语法）、`purify.min.js`（DOMPurify sanitize）
- [ ] 2.2 `chat.html` 引入两个库（`<script src="vendor/marked.min.js">` / `<script src="vendor/purify.min.js">`），确认 nginx 静态路径可达

## 3. 前端渲染实现

- [ ] 3.1 新增渲染函数：`renderMarkdown(text) -> html`——`marked.parse(text, {gfm:true, breaks:true})` → `DOMPurify.sanitize(html)`；链接加 `target=_blank rel=noopener noreferrer`
- [ ] 3.2 改造 `renderAiAnswerStream`：流式 token 追加改为节流（~120ms）整段重渲染（`renderMarkdown` 进 `innerHTML`），结束全量渲染；用户/系统消息保持 `escapeHtml` 纯文本
- [ ] 3.3 历史会话回放路径（`renderConversationHistory`）助手消息走同一 `renderMarkdown`
- [ ] 3.4 新增 markdown 元素 CSS（限定 `.bubble-content.md` 作用域）：标题层级、代码块背景/等宽、列表缩进、引用块、表格边框、行内代码、加粗——对齐设计稿

## 4. 验证

- [ ] 4.1 XSS 用例验证：构造含 `<img onerror>` / `<script>` / `javascript:` 的助手消息，确认被 DOMPurify 过滤（playwright 断言无脚本执行、无恶意元素）
- [ ] 4.2 真实对话验证：复现 trace_4a252403 的 web 兜底长文 markdown，确认标题/加粗/列表正确渲染、`[n]` 引用编号保留
- [ ] 4.3 流式体验验证：流式输出时格式渐进可见，结束时完整格式，无闪烁/卡顿
- [ ] 4.4 回归验证：既有聊天布局（气泡/输入区/澄清表单/侧边栏）不受 `.md` 样式影响
- [ ] 4.5 `pytest tests/ -q` 全量通过（后端未改，确认无回归）、ruff clean

## 5. 提交

- [ ] 5.1 设计稿与实现分开提交：先 `docs/design/` 设计稿 commit，再前端实现 commit，最后 vendor 依赖 commit（或按实际改动粒度分 2-3 个 commit）
