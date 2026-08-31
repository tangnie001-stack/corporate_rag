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
- **渲染配置**：`marked.parse(text, {gfm:true, breaks:true})`——GFM 启用表格/任务列表；`breaks:true` 单换行转 `<br>`，对齐 LLM 单换行输出习惯（设计稿 mockup 已验证；fenced 代码块/表格内部不受 breaks 影响）
- **异常兜底**：`renderMarkdown` 内部 try/catch，任何异常回退 `escapeHtml` 纯文本，保证回答内容永远可见（不因渲染失败丢失内容）
- **备选**：① micromark（dsh 方案）——增量流式性能最优但多文件依赖、集成复杂，对聊天级消息长度是过度设计；② markdown-it——功能等价 marked，但默认需配 sanitize 策略，选 marked 更省事
- **引入方式**：vendor 本地 JS（`deploy/nginx/html/vendor/marked.min.js`、`vendor/purify.min.js`），nginx 同目录托管，避免内网 CDN 不可达

### D2: 流式渲染策略 — 节流重渲染 + 中间态闭合补全 + 结束时全量渲染

- 流式中：每个 token 批次**节流（rAF/~60ms）**重渲染整段消息（marked 对未闭合语法容错，聊天级文本重解析成本可接受），保证流式时格式渐进可见
- **中间态闭合补全**：渲染前对预览文本做 `normalizePartialMarkdown`——` ``` ` 围栏奇数则补闭合符、行内反引号奇数则补一个，避免未闭合代码块/表格在流式中导致 DOM 结构反复变化（闪烁）；真正闭合符到达后被自然替换，用户无感知
- 结束：用**原始全文（不补全）**一次全量渲染（引用编号/完整表格等收尾）
- **异常中断兜底**：抽 `finalizeAnswer()` 统一最终渲染——`done`（正常结束）与 `error`/`source.onerror`（断连）共用，基于已累积的 `fullAnswer` 做最终全量渲染，避免断连时停留在带补全残留的落后预览
- **备选**：① dsh 的增量解析（冻结块+尾部重解析）——性能最优但对本项目过度；② 流式纯文本、结束才渲染——实现最简但流式时无格式，体验打折，不选
- **依据**（2026-08-31 调研）：主流方案（react-markdown 生态 + Markstream/streamdown）共识是"整段重渲染 + 节流 + 闭合补全"为充分务实的做法；节流与补全是纯 JS 能力，**静态 HTML 无需引入前端框架**（框架的价值在 AST 级增量更新，本场景不需要）

### D3: XSS 安全策略（纵深防御）

LLM/web 内容一律视为未信任内容。**验证发现的关键点**：DOMPurify 3.1.6 主路径用 DOMParser 解析不会触发 `img onerror`，但 legacy `createHTMLDocument`+`innerHTML` 回退路径存在解析期执行风险；marked 层转义保留作纵深防御。因此必须**先在 marked 侧中性化原生 HTML**，再让 DOMPurify 兜底：

- **第一层**：marked 自定义 `html` renderer，把原生 HTML（`token.text || token.raw`）转义为 `&lt;`/`&gt;`（实测 marked v12 会丢弃 inline html 返回，行为等同中性化；防御式 `||` 防 renderer 抛异常）
- **第二层**：`DOMPurify.sanitize(html)` 白名单过滤（剥 `onerror`/`javascript:` 等）
- 链接统一 `target=_blank rel=noopener noreferrer`
- 用户消息、系统消息继续 `escapeHtml` 纯文本，不进 markdown 路径

### D4: 样式与作用域

- markdown 元素 CSS 全部限定在 `.bubble-content.md` 类下（标题层级、代码块背景/等宽、列表缩进、引用块、表格边框、行内代码、加粗），避免污染现有聊天布局
- 样式规格按 `docs/agents/ui-design-flow.md` 走 `ui-ux-pro-max` 产出（`docs/design/pages/chat-markdown.md` + `chat-markdown-mockup.html`），对齐 MASTER.md 设计系统

### D5: 历史回放

- 会话历史加载路径（`renderConversationHistory`）助手消息同样走 markdown 渲染（同一渲染函数）

### D6: 滚动跟随 — stick 模式

- `scrollToBottom` 改为 stick 跟随：用户距底部 40px 以内才自动滚动到最新内容；主动上滑则暂停跟随，滑回底部恢复
- 目的：流式节流重渲染（innerHTML 重建）会打断用户向上翻阅历史，stick 滚动保证阅读不被新 chunk 拽回
- **备选**：保持无条件 `scrollToBottom`——实现最简，但用户翻阅历史时会被反复拽回底部，体验差，不选

### D7: 图片策略 — 严格 http(s) 白名单

- LLM/web 兜底内容可能含 markdown 图片 `![](url)`。**只允许 `http:`/`https:` 与相对路径**（`/`、`./`、`../`、`#`），**禁止一切 `data:`**（含 `data:image`）——DOMPurify 默认放行 `data:image`，必须自定义 `ALLOWED_URI_REGEXP` 收紧
- 理由：LLM/web 内容几乎无合法 base64 图片需求，`data:image` 体积大有内存/渲染风险，严格白名单更安全且与 spec 契约一致
- 图片加载失败不阻断回答内容
- **威胁模型**：若后续要求不允许第三方网络请求（内网隔离/合规场景），改为把图片按文本显示或接入图片代理，届时再评估；当前 web 兜底引用场景允许 http(s) 图片
- **依据**：Markstream 安全模型——safe 级允许普通 http(s) 链接和图片，但若威胁模型不允许第三方网络请求需用 escape 或图片代理

## Risks / Trade-offs

- [XSS：LLM 或 web 内容注入 HTML/脚本] → DOMPurify 白名单 sanitize + marked 禁用原生 HTML；用户消息不渲染 markdown
- [流式频繁重渲染导致卡顿] → rAF/~60ms 节流 + 中间态闭合补全；聊天级文本短，成本可控；异常可退回"结束才渲染"
- [流式重渲染打断用户阅读历史] → D6 stick 滚动（距底 40px 内才跟随）
- [vendor 文件部署遗漏] → vendor 目录随 `deploy/nginx/html/` 一起提交部署；验证时检查引用路径
- [markdown CSS 影响既有布局] → 全部样式限定 `.md` 作用域，playwright 回归验证
- [历史消息双渲染路径漂移] → 助手消息统一走同一渲染函数

## Migration Plan

1. `ui-ux-pro-max` 产出设计稿：`docs/design/pages/chat-markdown.md` + `docs/design/chat-markdown-mockup.html`（playwright 验证视觉）
2. vendor `marked.min.js` / `purify.min.js` 到 `deploy/nginx/html/vendor/`
3. `chat.html`：引入库 → 改 `renderAiAnswerStream` 走 markdown 渲染（节流 + 闭合补全 + 结束全量）→ `scrollToBottom` 改 stick 跟随 → 新增 `.md` CSS → 历史回放同路径
4. 验证：playwright 真实对话（含 web 兜底长文 markdown）、XSS 用例（注入 `<img onerror>`/`<script>` 应被过滤）
5. 部署生效：dev（docker-compose.yml 卷挂载 deploy/nginx/html）改文件即生效，无需重启；**生产（docker-compose.prod.yml 构建时 COPY）需 `docker compose -f docker-compose.prod.yml build nginx`（或 `up -d --build`）后生效**，且新 vendor/ 目录随镜像一起打包
