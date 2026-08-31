# chat-markdown-rendering 前端实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `deploy/nginx/html/chat.html` 的助手消息正确渲染 LLM 输出的 Markdown（流式渐进、安全净化、样式作用域隔离），替换当前纯文本平铺。

**Architecture:** 纯静态 HTML 单文件方案——vendor 本地 `marked` + `DOMPurify`，新增 `renderMarkdown` 渲染管线（marked html 转义 + DOMPurify 白名单双保险）；`renderAiAnswerStream` 改为 60ms 节流整段重渲染 + `normalizePartialMarkdown` 闭合补全，`finalizeAnswer()` 在 `done`/`error` 事件统一做最终全量渲染；`.bubble-content.md` 作用域 CSS；`scrollToBottom` 改 stick 跟随。全程无构建链、无 npm、不改后端。

**Tech Stack:** 原生 JS（无框架）/ marked v12（vendor）/ DOMPurify（vendor）/ playwright-cli（浏览器验证）/ nginx 静态托管

## Global Constraints

（以下精确值来自 `docs/openspec/changes/chat-markdown-rendering/` 的 design.md D1-D7 与 spec.md，实现必须逐字遵守）

- 渲染管线：`marked.parse(text, {gfm:true, breaks:true})` → `DOMPurify.sanitize(html, {ALLOWED_URI_REGEXP: STRICT_URI_REGEXP})` → `innerHTML`
- **marked 层先转义原生 HTML**（D3）：自定义 `html` renderer 把 `token.text || token.raw` 的 `<`/`>` 转义为 `&lt;`/`&gt;`——仅靠 DOMPurify 不够（解析期 `img onerror` 会先触发）
- 链接统一 `target="_blank" rel="noopener noreferrer"`
- 图片严格白名单（D7）：`img` 仅允许 http(s)/相对路径/`#`，**禁止一切 `data:`**（含 `data:image`）——`STRICT_URI_REGEXP = /^(?:https?:)?(?:\/\/|\/|#|\.\.?\/)/i`
- `renderMarkdown` 函数内 try/catch，任何异常回退 `escapeHtml(text)` 纯文本（D1 异常兜底）
- 流式节流 **60ms**（`STREAM_RENDER_INTERVAL = 60`）；流式预览用 `renderMarkdown(normalizePartialMarkdown(全文))`；`finalizeAnswer()` 用原始全文（不补全）最终渲染，**`done`（chat.html:1357）与 `error`/`source.onerror`（chat.html:1380/1395）共用**
- stick 滚动：用户距底部 **40px** 内才自动跟随，主动上滑暂停，滑回恢复
- markdown CSS 全部限定 `.bubble-content.md` 作用域，不得污染既有聊天布局
- 用户消息/系统消息/澄清表单保持 `escapeHtml` 纯文本，不进 markdown 路径
- vendor 库（`marked.min.js`/`purify.min.js`）随 `deploy/nginx/html/vendor/` 一起提交部署
- 不改后端；不引入 npm/构建链；注释与代码风格用中文、匹配 chat.html 现有风格

---

### Task 1: vendor 依赖引入

**Files:**
- Create: `deploy/nginx/html/vendor/marked.min.js`（复制自 `docs/design/vendor/marked.min.js`）
- Create: `deploy/nginx/html/vendor/purify.min.js`（复制自 `docs/design/vendor/purify.min.js`）
- Modify: `deploy/nginx/html/chat.html:884`（在 `<script>` 内联块之前插入两个库引用）

**Interfaces:**
- Consumes: 无
- Produces: 全局 `window.marked`（marked v12，含 `.parse`/`.use`）、全局 `window.DOMPurify`（含 `.sanitize`）——Task 2 依赖

- [ ] **Step 1: 复制 vendor 库文件**

```bash
mkdir -p /mnt/d/code/demo/AIAgent/corporate_rag/deploy/nginx/html/vendor
cp /mnt/d/code/demo/AIAgent/corporate_rag/docs/design/vendor/marked.min.js \
   /mnt/d/code/demo/AIAgent/corporate_rag/deploy/nginx/html/vendor/marked.min.js
cp /mnt/d/code/demo/AIAgent/corporate_rag/docs/design/vendor/purify.min.js \
   /mnt/d/code/demo/AIAgent/corporate_rag/deploy/nginx/html/vendor/purify.min.js
```

预期：两个文件复制成功，`ls deploy/nginx/html/vendor/` 显示 `marked.min.js`、`purify.min.js`。

- [ ] **Step 2: chat.html 引入两个库**

在 `chat.html:884` 的 `<script>` 内联块**之前**插入：

```html
<script src="vendor/marked.min.js"></script>
<script src="vendor/purify.min.js"></script>
```

确认插入后 nginx 静态路径可达（`vendor/` 与 `chat.html` 同目录）。

- [ ] **Step 3: playwright 验证库加载**

起本地静态服务后（`cd deploy/nginx/html && python3 -m http.server 8741`），用 playwright-cli goto `http://127.0.0.1:8741/chat.html` 并 eval：

```js
typeof window.marked?.parse === 'function' && typeof window.DOMPurify?.sanitize === 'function'
```

预期：返回 `true`（两个库都加载成功；marked v12 的 `window.marked` 是命名空间对象，必须检查 `.parse` 方法）。若失败，检查 `<script>` 路径相对 chat.html 是否正确。

- [ ] **Step 4: 提交**

```bash
cd /mnt/d/code/demo/AIAgent/corporate_rag
git add deploy/nginx/html/vendor/ deploy/nginx/html/chat.html
git commit -m "chore(chat): vendor marked/DOMPurify 本地依赖并引入 chat.html"
```

---

### Task 2: 渲染核心 renderMarkdown + normalizePartialMarkdown

**Files:**
- Modify: `deploy/nginx/html/chat.html`（在 `// ── Render Functions ──` 区域、`renderUserBubble` 之前新增渲染函数）

**Interfaces:**
- Consumes: Task 1 的 `window.marked`、`window.DOMPurify`；chat.html 既有 `escapeHtml(text)`（用户消息转义函数，已存在）
- Produces:
  - `renderMarkdown(text) -> string`（HTML 字符串，Task 3/4 依赖）
  - `normalizePartialMarkdown(text) -> string`（流式预览补全，Task 3 依赖）
  - `STRICT_URI_REGEXP`（模块级常量，Task 3 的 `renderMarkdown` 复用）

- [ ] **Step 1: 新增渲染函数（含安全配置）**

在 `// ── Render Functions ──` 之后、`renderUserBubble` 之前插入以下代码（`chat.html:954-956` 之间）：

```js
// ── Markdown 渲染（chat-markdown-rendering）──
// 图片严格白名单：仅 http(s)/相对路径/#，禁止一切 data:（D7）
const STRICT_URI_REGEXP = /^(?:https?:)?(?:\/\/|\/|#|\.\.?\/)/i;

// marked 安全配置（D3）：原生 HTML 先转义中性化，DOMPurify 只做纵深兜底
marked.use({
  renderer: {
    html(token) {
      const s = token.text || token.raw || '';
      return s.replace(/</g, '&lt;').replace(/>/g, '&gt;');
    },
    link(token) {
      const href = token.href || '#';
      const text = token.text || '';
      return `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${escapeHtml(text)}</a>`;
    }
  }
});

function renderMarkdown(text) {
  try {
    const html = marked.parse(text, { gfm: true, breaks: true });
    return DOMPurify.sanitize(html, { ALLOWED_URI_REGEXP: STRICT_URI_REGEXP });
  } catch (err) {
    return escapeHtml(text);
  }
}

// 流式中间态闭合补全：围栏/行内反引号奇数则补齐，避免未闭合代码块导致 DOM 结构反复变化（D2）
function normalizePartialMarkdown(text) {
  let result = text;
  const fenceCount = (result.match(/```/g) || []).length;
  if (fenceCount % 2 === 1) {
    result += '\n```';
  }
  const inlineTicks = (result.replace(/```/g, '').match(/`/g) || []).length;
  if (inlineTicks % 2 === 1) {
    result += '`';
  }
  return result;
}
```

- [ ] **Step 2: playwright 验证渲染与安全**

静态服务 + eval 以下断言，全部返回预期值：

```js
// ① 常规 markdown 渲染
renderMarkdown('# 标题\n\n**加粗**').includes('<h1>') && renderMarkdown('# 标题\n\n**加粗**').includes('<strong>')
// 预期: true

// ② 原生 HTML 被 marked 层转义（D3：onerror 不落地）
renderMarkdown('<img src=x onerror=alert(1)>').includes('&lt;img') && !renderMarkdown('<img src=x onerror=alert(1)>').includes('<img')
// 预期: true

// ③ 图片 data: 被过滤（D7）
!renderMarkdown('![](data:image/png;base64,abc)').includes('data:image')
// 预期: true

// ④ javascript: 链接被过滤
!renderMarkdown('[x](javascript:alert(1))').includes('javascript:')
// 预期: true

// ⑤ 链接带 target/rel
renderMarkdown('[x](https://a.com)').includes('target="_blank"') && renderMarkdown('[x](https://a.com)').includes('rel="noopener noreferrer"')
// 预期: true

// ⑥ 闭合补全：奇数围栏补闭合
normalizePartialMarkdown('```js\nconst a = 1') === '```js\nconst a = 1\n```'
// 预期: true

// ⑦ 异常兜底：marked 不抛（模拟破坏输入不会白屏——正常文本不触发，验证 try/catch 存在即可）
typeof renderMarkdown('## 测试') === 'string'
// 预期: true
```

- [ ] **Step 3: 提交**

```bash
cd /mnt/d/code/demo/AIAgent/corporate_rag
git add deploy/nginx/html/chat.html
git commit -m "feat(chat): renderMarkdown 渲染管线（安全双保险+异常兜底）与闭合补全"
```

---

### Task 3: 流式渲染改造（节流 + finalizeAnswer + done/error 挂钩）

**Files:**
- Modify: `deploy/nginx/html/chat.html:986-1003`（`renderAiAnswerStream` 整体替换）
- Modify: `deploy/nginx/html/chat.html:1357`（`done` 事件）、`1380`（`error` 事件）、`1395`（`source.onerror`）加 `finalizeAnswer()`
- Modify: `docs/design/pages/chat-markdown.md:72`（"120ms 节流" → "rAF/~60ms 节流"，同步 design D2）

**Interfaces:**
- Consumes: Task 2 的 `renderMarkdown(text)`、`normalizePartialMarkdown(text)`
- Produces:
  - `renderAiAnswerStream(text)`（保持原签名，token 事件照旧调用；内部改为累积 buffer + 60ms 节流渲染）
  - `finalizeAnswer()`（无参；最终全量渲染 + 复位流式状态；`done`/`error`/`onerror` 共用）
  - 模块级：`streamBuffer`（string）、`streamBubble`（Element|null）、`streamTimer`（number|null）、`STREAM_RENDER_INTERVAL`（const 60）

- [ ] **Step 1: 替换 renderAiAnswerStream 并新增流式状态**

将 `chat.html:986-1003` 的 `renderAiAnswerStream` 整体替换为：

```js
const STREAM_RENDER_INTERVAL = 60;
let streamBuffer = '';
let streamBubble = null;
let streamTimer = null;

// 获取或创建当前 AI 气泡（带 .md 作用域类）。基于 streamBubble 缓存复用，
// 不依赖 querySelector 时序（避免跨轮污染上一轮气泡）；finalizeAnswer 复位后自动新建。
function ensureAiBubble() {
  if (streamBubble && streamBubble.isConnected) {
    return streamBubble;
  }
  const div = document.createElement('div');
  div.className = 'bubble-row ai';
  div.innerHTML = '<div class="bubble-avatar">🤖</div><div class="bubble-content md"><div class="bubble-meta">系统</div></div>';
  chatContainer.appendChild(div);
  streamBubble = div.querySelector('.bubble-content');
  return streamBubble;
}

// 流式：累积 buffer，60ms 节流整段重渲染（预览走闭合补全）
function renderAiAnswerStream(text) {
  streamBuffer += text;
  streamBubble = ensureAiBubble();
  if (streamTimer === null) {
    streamTimer = setTimeout(() => {
      streamTimer = null;
      if (streamBubble) {
        streamBubble.innerHTML = renderMarkdown(normalizePartialMarkdown(streamBuffer));
        scrollToBottom();
      }
    }, STREAM_RENDER_INTERVAL);
  }
}

// 最终全量渲染：done/error/onerror 共用，原始全文（不补全），复位流式状态
function finalizeAnswer() {
  if (streamTimer !== null) {
    clearTimeout(streamTimer);
    streamTimer = null;
  }
  if (streamBubble) {
    streamBubble.innerHTML = renderMarkdown(streamBuffer);
    scrollToBottom();
  }
  streamBuffer = '';
  streamBubble = null;
}
```

注意：`token` 事件（chat.html:1318）的调用 `renderAiAnswerStream(data.token || '')` **保持不变**（签名兼容）。

- [ ] **Step 2: done/error/onerror 挂钩 finalizeAnswer**

在 `done` 事件回调开头（`chat.html:1358` `try {` 之后、`closeThinkRow()` 之前）加：

```js
      finalizeAnswer();
```

在 `error` 事件回调（`chat.html:1380`）`source.close();` 之后加：

```js
    finalizeAnswer();
```

在 `source.onerror`（`chat.html:1395`）回调开头加：

```js
    finalizeAnswer();
```

- [ ] **Step 3: 同步设计稿节流值**

`docs/design/pages/chat-markdown.md:72` 的"流式中：120ms 节流"改为：

```markdown
- 流式中：rAF/~60ms 节流整段重渲染（marked 对未闭合语法容错，预览前做闭合补全），格式渐进可见
```

- [ ] **Step 4: playwright 验证流式行为**

静态服务 + eval：

```js
// ① 模拟流式：分 3 次推送，断言 60ms 后内容渐进渲染、未闭合代码块被补全
streamBuffer = ''; streamBubble = null;
renderAiAnswerStream('# 标题\n\n```js\nconst a = 1');
renderAiAnswerStream('\nconst b = 2');
renderAiAnswerStream('\n```\n\n**加粗**');
await new Promise(r => setTimeout(r, 200));
document.querySelector('.bubble-row.ai:last-child .bubble-content.md').innerHTML.includes('<h1>')
// 预期: true

// ② finalizeAnswer 复位：无补全残留（最终态由原始全文渲染）
finalizeAnswer();
document.querySelector('.bubble-row.ai:last-child .bubble-content.md').innerHTML.includes('```js')
// 预期: false（原始全文以 fenced code 语义渲染，不残留字面 ```）

// ③ 幂等：无气泡时调用不抛错
finalizeAnswer();
// 预期: 无异常
```

- [ ] **Step 5: 提交**

```bash
cd /mnt/d/code/demo/AIAgent/corporate_rag
git add deploy/nginx/html/chat.html docs/design/pages/chat-markdown.md
git commit -m "feat(chat): 流式节流渲染 + finalizeAnswer（done/error 统一收尾）"
```

---

### Task 4: 历史回放 markdown + .md 样式 + stick 滚动

**Files:**
- Modify: `deploy/nginx/html/chat.html:975-984`（`renderAiAnswer` 历史回放走 `renderMarkdown` + 加 `md` 类）
- Modify: `deploy/nginx/html/chat.html:156`（`.bubble-row.user .bubble-content` 之后新增 `.md` CSS）
- Modify: `deploy/nginx/html/chat.html:924-928`（`scrollToBottom` 改 stick）与初始化处加 `scroll` 监听

**Interfaces:**
- Consumes: Task 2 的 `renderMarkdown(text)`；Task 3 的 `scrollToBottom()`
- Produces: 模块级 `stickToBottom`（boolean，初始 true）——无其他 task 依赖

- [ ] **Step 1: renderAiAnswer 走 markdown 渲染**

将 `chat.html:975-984` 的 `renderAiAnswer` 中 `bubble-content` 改为带 `md` 类并用 `renderMarkdown`：

```js
function renderAiAnswer(text) {
  const div = document.createElement('div');
  div.className = 'bubble-row ai';
  div.innerHTML = `
    <div class="bubble-avatar">🤖</div>
    <div class="bubble-content md"><div class="bubble-meta">系统</div>${renderMarkdown(text)}</div>
  `;
  chatContainer.appendChild(div);
  scrollToBottom();
}
```

（即：`bubble-content` → `bubble-content md`，`escapeHtml(text)` → `renderMarkdown(text)`。）

- [ ] **Step 2: 新增 .bubble-content.md CSS**

在 `chat.html:156` 的 `.bubble-row.user .bubble-content { ... }` 块之后插入：

```css

  /* ── Markdown 渲染（.bubble-content.md 作用域，对齐 docs/design/pages/chat-markdown.md）── */
  .bubble-content.md {
    overflow-wrap: anywhere;
  }
  .bubble-content.md > *:first-child { margin-top: 0; }
  .bubble-content.md > *:last-child { margin-bottom: 0; }
  .bubble-content.md h1,
  .bubble-content.md h2 { font-size: 16px; font-weight: 700; margin: 20px 0 8px; }
  .bubble-content.md h3 { font-size: 15px; font-weight: 700; margin: 16px 0 6px; }
  .bubble-content.md h4,
  .bubble-content.md h5,
  .bubble-content.md h6 { font-size: 14px; font-weight: 600; }
  .bubble-content.md p { margin: 0 0 12px; }
  .bubble-content.md strong { font-weight: 600; }
  .bubble-content.md code {
    font-family: 'Source Code Pro', ui-monospace, SFMono-Regular, Consolas, monospace;
    font-size: 13px;
    background: rgba(219, 234, 254, 0.25);
    border-radius: 4px;
    padding: 1px 5px;
  }
  .bubble-content.md pre {
    background: #F1F5F9;
    border-radius: 8px;
    padding: 12px;
    overflow-x: auto;
    margin: 12px 0;
  }
  .bubble-content.md pre code {
    background: transparent;
    padding: 0;
    border-radius: 0;
  }
  .bubble-content.md a { color: var(--primary); text-decoration: none; }
  .bubble-content.md a:hover { text-decoration: underline; }
  .bubble-content.md hr { border: 0; border-top: 1px solid var(--border); margin: 16px 0; }
  .bubble-content.md ul,
  .bubble-content.md ol { padding-left: 18px; margin: 8px 0; }
  .bubble-content.md li { margin: 6px 0; }
  .bubble-content.md li::marker { color: var(--text-secondary); }
  .bubble-content.md blockquote {
    border-left: 3px solid var(--primary);
    padding-left: 12px;
    margin: 8px 0;
    color: var(--text-secondary);
  }
  .bubble-content.md table {
    display: block;
    max-width: 100%;
    overflow-x: auto;
    border-collapse: collapse;
    margin: 12px 0;
  }
  .bubble-content.md table th {
    font-weight: 600;
    background: #F1F5F9;
    border-bottom: 1px solid var(--border);
    padding: 6px 10px;
  }
  .bubble-content.md table td {
    border-bottom: 1px solid var(--border);
    padding: 6px 10px;
  }
  .bubble-content.md input[type="checkbox"] { accent-color: var(--primary); }
```

- [ ] **Step 3: scrollToBottom 改 stick 跟随**

将 `chat.html:924-928` 的 `scrollToBottom` 替换为：

```js
let stickToBottom = true;

function scrollToBottom() {
  if (!stickToBottom) return;
  requestAnimationFrame(() => {
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  });
}

// 用户上滑（距底 > 40px）暂停自动跟随；滑回底部恢复
function onChatScroll() {
  const distance = document.body.scrollHeight - window.innerHeight - window.scrollY;
  stickToBottom = distance < 40;
}
```

在初始化区域（`chat.html` 尾部，`loadSessions()` 调用附近）加一行滚动监听：

```js
window.addEventListener('scroll', onChatScroll);
```

- [ ] **Step 4: playwright 验证历史回放与样式**

```js
// ① 历史回放：renderAiAnswer 渲染 markdown
document.querySelectorAll('.bubble-row.ai').forEach(el => el.remove());
renderAiAnswer('# 标题\n\n| a | b |\n|---|---|\n| 1 | 2 |');
const md = document.querySelector('.bubble-row.ai:last-child .bubble-content.md');
md.innerHTML.includes('<h1>') && md.innerHTML.includes('<table>')
// 预期: true

// ② md 类存在（CSS 命中）
document.querySelector('.bubble-row.ai:last-child .bubble-content').classList.contains('md')
// 预期: true

// ③ stick 滚动：上滑后 scrollToBottom 不强制滚动
stickToBottom = false;
scrollToBottom(); // 不滚动，无异常
stickToBottom = true;
// 预期: 无异常；scrollToBottom 在 stick=true 时正常执行
```

- [ ] **Step 5: 提交**

```bash
cd /mnt/d/code/demo/AIAgent/corporate_rag
git add deploy/nginx/html/chat.html
git commit -m "feat(chat): 历史回放 markdown 渲染 + .md 作用域样式 + stick 滚动"
```

---

### Task 5: 综合验证（XSS / 真实对话 / 回归）

**Files:**
- 无新增代码（如验证发现问题，作为修复 commit 追加）

**Interfaces:**
- Consumes: Task 1-4 全部改动

- [ ] **Step 1: playwright XSS/协议全量用例**

静态服务 + eval 断言（注意：marked 转义后危险负载会以**可见文本**形式存在，因此断言必须检查"解析后的 DOM 无危险元素"而非"字符串不含关键字"）：

```js
const payloads = [
  '<img src=x onerror=alert(1)>',
  '<script>alert(1)</script>',
  '[x](javascript:alert(1))',
  '![](data:image/png;base64,abc)',
  '<svg onload=alert(1)>x</svg>',
  '<iframe src="https://evil.com"></iframe>',
];
const test = (p) => {
  const host = document.createElement('div');
  host.innerHTML = renderMarkdown(p);
  return host.querySelector('img, script, svg, iframe, object, embed') === null;
};
payloads.every(test)
// 预期: true（所有向量渲染后无危险元素落地——被 marked 转义为文本或被 DOMPurify 剥离）
```

- [ ] **Step 2: 真实对话集成验证（需后端）**

`docker compose up -d` 起后端后，用 playwright-cli 打开实际页面，发起一个会命中 web 兜底的查询（复现 trace_4a252403 形态的长文 markdown），断言：
- 流式过程中出现 `h3`/`strong`/`li` 元素（格式渐进可见）
- 结束时表格/列表完整，`[n]` 引用编号文本保留
- 无 ```` ``` ```` 字面残留（未闭合被最终渲染收尾）
- 引用区（citation）与既有布局不受影响

- [ ] **Step 3: 回归验证**

- 用户消息/系统消息仍为纯文本（`escapeHtml`，无 markdown 解析）
- 澄清表单（`ask_user` composer）、思考折叠行、侧边栏、引用卡片布局无回归（playwright 对比截图或 DOM 结构断言）
- 滚动：生成长回答时 stick 行为正常（上滑不被打断）

- [ ] **Step 4: 后端回归**

```bash
cd /mnt/d/code/demo/AIAgent/corporate_rag
pytest tests/ -q
ruff check .
```

预期：全部通过、无新增 error（本 change 未改后端，应零回归）。

- [ ] **Step 5: 最终检查与提交**

- 确认 `deploy/nginx/html/vendor/` 已提交、`docs/design/vendor/` 与 `deploy/nginx/html/vendor/` 文件一致
- 确认无遗留 `console.log` 调试代码、无 TODO
- 如验证发现问题：修复并追加 commit；如全部通过则无需新 commit

```bash
git log --oneline -8
```

预期：显示 Task 1-4 的 4 个 feat/chore commit。
