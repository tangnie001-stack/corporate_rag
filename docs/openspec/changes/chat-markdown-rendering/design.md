## Context

前端 `deploy/nginx/html/chat.html` 是纯静态单文件（nginx 托管、无构建链、无 npm），`renderAiAnswerStream` 用 `document.createTextNode` 把助手 token 当纯文本追加，LLM 输出的 Markdown 语法原样平铺显示。web-search-fallback 上线后 web 兜底答案是长文结构化 Markdown，问题凸显。

参考 deepseek-harness：`packages/client/ui-primitives/src/markdown/MarkdownText.tsx` 用 micromark（GFM+数学）做**流式增量渲染**（冻结块+尾部重解析），并对未信任内容做 sanitize。

约束：
- 无构建链 → 渲染库必须 vendor 本地 JS 文件（随 `deploy/nginx/html/` 一起部署）或 CDN 引入
- 助手消息内容来自 LLM 与 web 搜索结果，是**未信任内容**，必须 sanitize
- 现有 `[n]` 引用编号文本、citation 事件、用户消息的 `escapeHtml` 行为不能破坏

## Goals / Non-Goals

**Goals:**
- 助手消息正确渲染 Markdown（标题/加粗/列表/代码块/引用/表格/行内代码）
- 流式输出时能即时看到格式（或至少在结束时渲染完整格式）
- XSS 安全：未信任内容渲染必须 sanitize
- markdown 元素样式对齐全局设计系统（docs/design/MASTER.md）
- 历史会话回放同样走 markdown 渲染

**Non-Goals:**
- 代码语法高亮（prism/highlight.js，后置）
- LaTeX 数学公式渲染（dsh 有，非必需）
- 用户消息/系统消息/澄清表单的渲染改造（保持纯文本，防注入面最小化）
- web 引用缺失问题（独立 issue，另议）

## Decisions

### D1: 渲染库选型 — `marked` + `DOMPurify`（vendor 本地化）

- `marked`：单文件、零依赖、对未闭合语法天然容错（流式中途友好）、体积小易 vendor
- `DOMPurify`：白名单 sanitize，成熟可靠，防 LLM/web 内容 XSS
- 渲染管线：`marked.parse(text) → DOMPurify.sanitize(html) → bubble.innerHTML`
- **备选**：① micromark（dsh 方案）——增量流式性能最优但多文件依赖、集成复杂，对聊天级消息长度是过度设计；② markdown-it——功能等价 marked，但默认需配 sanitize 策略，选 marked 更省事
- **引入方式**：vendor 本地 JS（`deploy/nginx/html/vendor/marked.min.js`、`vendor/purify.min.js`），nginx 同目录托管，避免内网 CDN 不可达

### D2: 流式渲染策略 — 节流重渲染 + 结束时全量渲染

- 流式中：每个 token 批次**节流（~120ms）**重渲染整段消息（marked 对未闭合语法容错，聊天级文本重解析成本可接受），保证流式时格式渐进可见
- 结束：一次全量渲染（引用编号/完整表格等收尾）
- **备选**：① dsh 的增量解析（冻结块+尾部重解析）——性能最优但对本项目过度；② 流式纯文本、结束才渲染——实现最简但流式时无格式，体验打折，不选

### D3: XSS 安全策略（纵深防御）

LLM/web 内容一律视为未信任内容。**验证发现的关键点**：仅靠 `DOMPurify.sanitize` 不够——DOMPurify 解析含 `onerror` 的 `<img>` 时，浏览器会在属性剥离**之前**执行一次 handler（实测 `alert(1)` 触发）。因此必须**先在 marked 侧中性化原生 HTML**，再让 DOMPurify 兜底：

- **第一层**：marked 自定义 `html` renderer，把原生 HTML（`token.text || token.raw`）转义为 `&lt;`/`&gt;`（实测 marked v12 会丢弃 inline html 返回，行为等同中性化；防御式 `||` 防 renderer 抛异常）
- **第二层**：`DOMPurify.sanitize(html)` 白名单过滤（剥 `onerror`/`javascript:` 等）
- 链接统一 `target=_blank rel=noopener noreferrer`
- 用户消息、系统消息继续 `escapeHtml` 纯文本，不进 markdown 路径

### D4: 样式与作用域

- markdown 元素 CSS 全部限定在 `.bubble-content.md` 类下（标题层级、代码块背景/等宽、列表缩进、引用块、表格边框、行内代码、加粗），避免污染现有聊天布局
- 样式规格按 `docs/agents/ui-design-flow.md` 走 `ui-ux-pro-max` 产出（`docs/design/pages/chat-markdown.md` + `chat-markdown-mockup.html`），对齐 MASTER.md 设计系统

### D5: 历史回放

- 会话历史加载路径（`renderConversationHistory`）助手消息同样走 markdown 渲染（同一渲染函数）

## Risks / Trade-offs

- [XSS：LLM 或 web 内容注入 HTML/脚本] → DOMPurify 白名单 sanitize + marked 禁用原生 HTML；用户消息不渲染 markdown
- [流式频繁重渲染导致卡顿] → 120ms 节流；聊天级文本短，成本可控；异常可退回"结束才渲染"
- [vendor 文件部署遗漏] → vendor 目录随 `deploy/nginx/html/` 一起提交部署；验证时检查引用路径
- [markdown CSS 影响既有布局] → 全部样式限定 `.md` 作用域，playwright 回归验证
- [历史消息双渲染路径漂移] → 助手消息统一走同一渲染函数

## Migration Plan

1. `ui-ux-pro-max` 产出设计稿：`docs/design/pages/chat-markdown.md` + `docs/design/chat-markdown-mockup.html`（playwright 验证视觉）
2. vendor `marked.min.js` / `purify.min.js` 到 `deploy/nginx/html/vendor/`
3. `chat.html`：引入库 → 改 `renderAiAnswerStream` 走 markdown 渲染 → 新增 `.md` CSS → 历史回放同路径
4. 验证：playwright 真实对话（含 web 兜底长文 markdown）、XSS 用例（注入 `<img onerror>`/`<script>` 应被过滤）
5. `docker compose restart` 或刷新 nginx 静态（chat.html 是静态文件，nginx 直接服务，改文件即生效，无需重启 app）
