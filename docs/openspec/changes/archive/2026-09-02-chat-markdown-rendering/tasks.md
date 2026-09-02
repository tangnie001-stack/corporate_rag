# chat-markdown-rendering Tasks

## 1. UI 设计稿（前置）

- [x] 1.1 用 `ui-ux-pro-max` skill 生成页面规格：`docs/design/pages/chat-markdown.md`（中文，含 markdown 元素视觉规格——标题层级/代码块/列表/引用块/表格/行内代码/加粗，对齐 `docs/design/MASTER.md` 全局设计系统）
- [x] 1.2 产出效果预览：`docs/design/chat-markdown-mockup.html`（独立 HTML，含典型助手 markdown 长文 + 代码块 + 表格样例）
- [x] 1.3 用 playwright-cli 验证 mockup 视觉（标题/代码块/列表/表格渲染正常，不截图、提交 HTML）

## 2. 前端依赖引入

- [ ] 2.1 vendor 本地 JS 到 `deploy/nginx/html/vendor/`：`marked.min.js`（含完整 Markdown 语法）、`purify.min.js`（DOMPurify sanitize）
- [ ] 2.2 `chat.html` 引入两个库（`<script src="vendor/marked.min.js">` / `<script src="vendor/purify.min.js">`），确认 nginx 静态路径可达

## 3. 前端渲染实现

- [ ] 3.1 新增渲染函数：`renderMarkdown(text) -> html`——`marked.parse(text, {gfm:true, breaks:true})` → `DOMPurify.sanitize(html)`；原始 HTML 在 marked 层先转义（html renderer 中和 `onerror` 等，设计稿 D3 安全方案）；链接加 `target=_blank rel=noopener noreferrer`；图片 `img` 仅允许 http(s)/相对路径，**自定义 `ALLOWED_URI_REGEXP` 禁止 `data:`**（D7 图片策略）；**函数内 try/catch 异常回退 `escapeHtml` 纯文本**（D1 异常兜底）
- [ ] 3.2 新增流式中间态补全函数：`normalizePartialMarkdown(text)`——` ``` ` 围栏奇数则补闭合符、行内反引号奇数则补一个，避免流式中未闭合代码块/行内代码导致 DOM 结构反复变化（闪烁）
- [ ] 3.3 改造 `renderAiAnswerStream`：逐 token 追加改为节流（rAF/~60ms）整段重渲染——流式预览用 `renderMarkdown(normalizePartialMarkdown(全文))` 进 `innerHTML`；**抽 `finalizeAnswer()` 统一最终全量渲染，`done` 事件（chat.html:1357）与 `error`/`source.onerror`（chat.html:1380/1395）共用**；用户/系统消息保持 `escapeHtml` 纯文本
- [ ] 3.4 历史会话回放路径：`loadSessionMessages` 的助手消息渲染函数 `renderAiAnswer`（chat.html:1517/961）改走同一 `renderMarkdown`
- [ ] 3.5 新增 markdown 元素 CSS（限定 `.bubble-content.md` 作用域）：标题层级、代码块背景/等宽、列表缩进、引用块、表格边框、行内代码、加粗——对齐设计稿
- [ ] 3.6 滚动改造：`scrollToBottom` 改为 stick 跟随——用户距底部 40px 内才自动滚动，主动上滑暂停跟随，滑回底部恢复（流式重渲染不打断用户阅读）

## 4. 验证

- [ ] 4.1 XSS/协议用例验证：构造含 `<img onerror>` / `<script>` / `javascript:` 链接 / `![](javascript:)` / `![](data:)` 的助手消息，确认被 DOMPurify 过滤或协议拦截（playwright 断言无脚本执行、无恶意元素、无不安全协议请求）
- [ ] 4.2 真实对话验证：复现 trace_4a252403 的 web 兜底长文 markdown，确认标题/加粗/列表正确渲染、`[n]` 引用编号保留
- [ ] 4.3 流式体验验证：流式输出时格式渐进可见，未闭合代码块/表格在流式期间布局稳定不闪烁，结束时完整格式，无闪烁/卡顿
- [ ] 4.4 回归验证：既有聊天布局（气泡/输入区/澄清表单/侧边栏）不受 `.md` 样式影响
- [ ] 4.5 `pytest tests/ -q` 全量通过（后端未改，确认无回归）、ruff clean

## 5. 提交

- [ ] 5.1 设计稿与实现分开提交：先 `docs/design/` 设计稿 commit，再前端实现 commit，最后 vendor 依赖 commit（或按实际改动粒度分 2-3 个 commit）
