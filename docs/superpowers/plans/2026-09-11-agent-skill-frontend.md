# 智能体/技能 前端实现 Plan（Plan 4 / 4）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `deploy/nginx/html/chat.html` 落地两个选择器（智能体＝会话级、技能＝消息级）、输入框 `/` 命令补全、请求体携带会话绑定的 `agent`、`agent_used` 静默纠正与历史页顶栏只读回显，并同步设计文档。

**Architecture:** `chat.html` 是**自包含单文件**（内联 `<style>` + `<script>`，无构建步骤、无外部 `css/`/`js/` 引用）。全部改动落在该文件：新增两个 DOM 块与配套 CSS、把候选数据从 `GET /api/agents` / `GET /api/skills` 拉进内存、复用既有 `.kb-menu` / `.kb-menu-item` 浮层范式构建两个下拉、在 `startStream` 的请求体补 `agent`、在 `buildStreamHandlers` 补 `agent_used`。图标一律**内联 SVG**（Lucide path），**不引入图标库**。

**Tech Stack:** 原生 HTML/CSS/JS（无框架、无构建）。验证工具 `playwright-cli`。**无新增依赖。**

**Spec:**
- 页面规格：`docs/design/pages/chat-agent-skill-selector-2026-09-11.md`（**唯一视觉依据**）
- 效果预览：`docs/design/chat-agent-skill-selector-mockup-2026-09-11.html`
- change 任务：`docs/openspec/changes/session-agent-and-skill-invocation/tasks.md` §6（6.1–6.7）
- 设计依据：同 change 的 `design.md` D21（UI 落位与两个入口形态）
- 流程：`docs/agents/ui-design-flow.md`

## Global Constraints

- **每个前端任务的实现必须调用 `frontend-design` skill 落地**（change tasks.md §6 的节首约定）；视觉方向、排版、交互动效**以规格 `pages/chat-agent-skill-selector-2026-09-11.md` 为唯一依据，不重新发散设计**；改完用 `playwright-cli` 对照设计稿验证。
- **原型只作参考**：`chat-agent-skill-selector-mockup-2026-09-11.html` 给可用的内联 SVG path 与交互形态，但**页面结构以 chat.html 既有范式为准**（不要照搬 mockup 的骨架）。
- 注释一律**中文**；新增 CSS 变量一律复用既有变量（`--primary` / `--primary-light` / `--surface` / `--border` / `--bg` / `--text` / `--text-secondary` / `--text-muted` / `--shadow` / `--shadow-lg`），**不新增颜色字面量**。
- 图标一律**内联 SVG**（`viewBox="0 0 24 24"`、`stroke="currentColor"`、`stroke-width="2"`），**不使用 emoji**。
- 新选择器与「知识库选择器 / 深度思考 chip」**同构**：智能体胶囊复制 `.kb-trigger` 的样式与 `role`/`aria` 属性结构；技能 chip 复制 `.thinking-chip` 的样式结构。
- **不新增第三方库**；不改 `login.html` / `index.html`；不改后端。
- 所有新增交互必须满足规格 §无障碍：`role="button"` + `aria-haspopup="listbox"` + `aria-expanded` 随开合切换 + `tabindex="0"`；菜单 `role="listbox"`、项 `role="option"` + `aria-selected`；键盘 Enter/Space 开合、↑/↓ 移动、Enter 选中、Esc 关闭并**归还焦点**；焦点环 `box-shadow: 0 0 0 3px rgba(59,130,246,0.3)`；过渡 150ms 且 `prefers-reduced-motion` 取消动画。
- 两个新菜单与引用抽屉 / 任务面板**互斥单开**（复用/扩展既有 `closeAllDrawers` 思路）。
- 契约：`agent` 取值是**预设 `name`（ASCII slug）**；默认项前端合成 `value=""`；`sessions/list` 项的字段名是 `agent`；流事件名是 `agent_used`。
- 文档防腐：实现后按 `ui-design-flow.md` 同步 `docs/design/MASTER.md` 与 `pages/chat-agent-skill-selector-2026-09-11.md`（保证设计与实现一致）。

## 既有落点速查（已实机核对，行号为改动前的 `chat.html`）

| 用途 | 位置 |
|---|---|
| 新对话页 `kb-bar`（智能体胶囊要插的知识库胶囊右侧） | `chat.html:723-738`；`.kb-bar` CSS `:159`（**当前无 `gap`**） |
| `.kb-trigger` 样式（智能体胶囊照抄） | CSS `:160-173` |
| 新对话页 composer（技能 chip 要插的深度思考右侧） | `chat.html:740-757`；`.composer-bottom` CSS `:222` |
| `.thinking-chip` 样式（技能 chip 照抄） | CSS `:226-237` |
| 历史对话页 composer | `chat.html:783-802` |
| 发送入口 | `initComposerEvents` `:2432-2447`；`sendMessage` `:2328-2355`；`startStream` `:2301-2325`（body 在 `:2317-2322`） |
| SSE 传输与分发 | `fetchStream` `:2036-2064`；`parseSSE` `:2066-2078`；`buildStreamHandlers` `:2086-2238`（`model_info` 在 `:2160`） |
| 模型信息渲染 | `renderModelInfo` `:1643-1652` |
| KB 下拉范式（菜单项/单选点） | `openKbMenu` `:2503` / `closeKbMenu` `:2508` / `renderKbMenu` `:2495`；CSS `.kb-menu` `:176-180`、`.kb-menu-item` `:184-190`、`.check` `:194-200` |
| 会话列表与顶栏 | `loadSessions` `:2604-2634`（`sessionMap` 在 `:2620`）；`updateHeader` `:974-977`；`syncHeaderFromSession` `:980-989`；`switchSession` `:2832-2843` |
| 抽屉/面板互斥 | `closeAllDrawers` `:1548-1551`（Esc 用它 `:2929-2938`） |
| 全局 state | `:847-857` |
| 登录门禁 / trace id | `boot` `:2958-3021`；`verifyLogin` `:2862`；`generateTraceId` `:2575-2577` |

---

### Task 1: 智能体选择器（新建对话页，会话级、默认项「默认」）

**Files:**
- Modify: `deploy/nginx/html/chat.html`（CSS：`.kb-bar` 加 `gap`；新增 `.agent-*`；HTML：`kb-bar` 内加胶囊 + 菜单；JS：`state.agent`、`loadAgents`、`renderAgentMenu`、`openAgentMenu`/`closeAgentMenu`、选中处理）

**Interfaces:**
- Consumes：`GET /api/agents` → `{code, data: {agents: [{name, display_name, description}]}}`（Plan 3 T9）
- Produces：
  - `state.agent: string`（`""` = 默认/未绑定；否则预设 `name`）
  - `state.agents: Array<{name, display_name, description}>`
  - `loadAgents()` / `renderAgentMenu()` / `openAgentMenu()` / `closeAgentMenu()` / `selectAgent(name)`

- [ ] **Step 1: 按规格落 CSS（先读规格再动手）**

读 `docs/design/pages/chat-agent-skill-selector-2026-09-11.md` L31–L62，按其精确值实现。`.kb-bar`（`chat.html:159`）末尾补 `gap:8px;`；新增块（颜色全部用既有变量）：

```css
/* 智能体选择器（会话级）：与 .kb-trigger 同构，位于知识库胶囊右侧 */
.agent-trigger{display:inline-flex;align-items:center;gap:7px;padding:7px 14px;background:var(--surface);border:1px solid var(--border);border-radius:10px;font-size:13px;color:var(--text);box-shadow:var(--shadow);transition:border-color 150ms,background 150ms;cursor:pointer}
.agent-trigger:hover{border-color:var(--primary)}
.agent-trigger:focus-visible{box-shadow:0 0 0 3px rgba(59,130,246,0.3)}
.agent-trigger svg{width:14px;height:14px}
.agent-trigger .chevron{width:12px;height:12px;opacity:.6;margin-left:2px}
.agent-name{color:var(--primary);font-weight:500}
.agent-name.none{color:var(--text-secondary);font-weight:400}
.agent-menu{position:absolute;top:calc(100% + 6px);left:0;min-width:300px;background:var(--surface);border:1px solid var(--border);border-radius:12px;box-shadow:var(--shadow-lg);z-index:30;display:none}
.agent-menu.open{display:block}
.agent-menu-title{padding:10px 14px 6px;font-size:11px;color:var(--text-muted);letter-spacing:.08em}
.agent-item{display:flex;align-items:center;gap:10px;padding:9px 14px;cursor:pointer}
.agent-item:hover{background:var(--bg)}
.agent-item-info{flex:1;min-width:0}
.agent-item-name{font-size:13px;color:var(--text)}
.agent-item-desc{font-size:12px;color:var(--text-secondary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.agent-menu-foot{padding:8px 14px;border-top:1px solid var(--border);font-size:11px;color:var(--text-muted)}
@media (prefers-reduced-motion:reduce){.agent-trigger{transition:none}}
```

- [ ] **Step 2: 落 HTML（`chat.html:728` 之后、`#kbMenu` 之前）**

```html
            <div class="agent-trigger" id="agentTrigger" tabindex="0" role="button" aria-haspopup="listbox" aria-expanded="false">
              <!-- Lucide user-round（内联，按 mockup 取 path） -->
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-6 8-6s8 2 8 6"/></svg>
              <span class="agent-name none" id="agentLabel">默认</span>
              <svg class="chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg>
            </div>
            <div class="agent-menu" id="agentMenu" role="listbox" aria-label="选择智能体">
              <div class="agent-menu-title">选择智能体</div>
              <div id="agentList"></div>
              <div class="agent-menu-foot">选定后本会话内不可更改</div>
            </div>
```

- [ ] **Step 3: 落 JS（state + 数据加载 + 渲染 + 开合）**

`state`（`chat.html:847-857`）加：

```js
  agent: '',            // 本会话绑定的智能体预设名（'' = 默认/未绑定）
  agents: [],           // GET /api/agents 的候选（含前端合成的「默认」项）
```

新增函数（放 `renderKbMenu`/`openKbMenu` 附近，复用其写法）：

```js
// 加载智能体候选：首项为前端合成的「默认」（value=''），失败静默降级为仅「默认」
async function loadAgents(){
  const fallback = [{ name:'', display_name:'默认', description:'通用助手' }];
  try{
    const resp = await fetch('/api/agents', { headers:{ 'X-Trace-ID': generateTraceId() } });
    const body = await resp.json();
    const list = (body && body.data && Array.isArray(body.data.agents)) ? body.data.agents : [];
    state.agents = fallback.concat(list);
  }catch(e){ state.agents = fallback; }
  renderAgentMenu();
}

// 渲染菜单：选中态用 aria-selected + .check 圆点（与 kb-menu-item 同构）
function renderAgentMenu(){
  const box = $('agentList');
  if(!box) return;
  const items = state.agents.map(function(a){
    const selected = (a.name === state.agent);
    const desc = a.description || '';
    return '<div class="agent-item" role="option" tabindex="-1" aria-selected="' + (selected?'true':'false') + '" data-name="' + a.name + '">'
      + '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-6 8-6s8 2 8 6"/></svg>'
      + '<span class="agent-item-info"><span class="agent-item-name">' + escapeHtml(a.display_name || a.name) + '</span>'
      + (desc ? '<span class="agent-item-desc">' + escapeHtml(desc) + '</span>' : '') + '</span>'
      + '<span class="check"' + (selected?' data-on="1"':'') + '></span></div>';
  }).join('');
  box.innerHTML = items;
  Array.prototype.forEach.call(box.querySelectorAll('.agent-item'), function(el){
    el.addEventListener('click', function(){ selectAgent(el.getAttribute('data-name')); });
  });
}

// 选中：单选、再点当前项不取消；更新胶囊文案与样式
function selectAgent(name){
  state.agent = name || '';
  const label = $('agentLabel');
  if(label){
    label.textContent = state.agent ? state.agent : '默认';
    label.className = state.agent ? 'agent-name' : 'agent-name none';
  }
  renderAgentMenu();
  closeAgentMenu();
}

function openAgentMenu(){ $('agentMenu').classList.add('open'); $('agentTrigger').setAttribute('aria-expanded','true'); }
function closeAgentMenu(){ $('agentMenu').classList.remove('open'); $('agentTrigger').setAttribute('aria-expanded','false'); }
```

- [ ] **Step 4: 接线（初始加载 + 开合 + 点外关闭 + 新建会话重置）**

- `boot()` 内（登录通过后）调用 `loadAgents();`（与既有 `loadKBs()` 同批）。
- `initComposerEvents` 之外，仿 KB 触发器的既有写法绑 `#agentTrigger` 的 `click`（切换开合）与 `keydown`（Enter/Space 开合）。
- `document` 的既有 click-outside 处理（`chat.html:2565-2571` 一带）补 `closeAgentMenu()`。
- `newSession()`（`:2846-2857`）里补 `selectAgent('');`（新会话回到「默认」）。
- **`switchSession(...)`（历史会话）不渲染该胶囊**（T4 处理顶栏回显）。

- [ ] **Step 5: playwright-cli 对照设计稿验证**

```
playwright-cli open http://localhost/chat.html     # 需要登录（cookie token）
playwright-cli snapshot
# 断言：kb-bar 行内「知识库胶囊 在左、智能体胶囊 紧邻其右、间距 8px、不换行」
# 断言：智能体胶囊默认文案为「默认」、字色为次级色、字重 400
# 点击胶囊 → 菜单在下方弹出，首项「默认 / 通用助手」，底部有「选定后本会话内不可更改」
playwright-cli resize 375 800                      # 收到 <720px 时两胶囊不换行
```
把核对结论写进报告（含 `playwright-cli snapshot` 的落盘路径）。

- [ ] **Step 6: 提交**

```bash
git add deploy/nginx/html/chat.html
git commit -m "feat(ui): 新建对话页智能体选择器（会话级、默认项「默认」、菜单底锁提示）"
```

---

### Task 2: 技能选择器（消息级 chip + 向上弹出菜单 + 插入 `/name `）

**Files:**
- Modify: `deploy/nginx/html/chat.html`（CSS：`.skill-chip` + `.skill-menu`（向上）；HTML：两个 composer 各加 chip；JS：`loadSkills`/`renderSkillMenu`/`openSkillMenu`/`closeSkillMenu`/`selectSkill`/`insertSkillPrefix`）

**Interfaces:**
- Consumes：`GET /api/skills` → `{code, data: {skills: [{name, description}]}}`（服务端已过滤 `user-invocable:false`）
- Produces：
  - `state.skills: Array<{name, description}>`；`state.skill: string`（`""` = 不使用技能）
  - `insertSkillPrefix(name)` —— 向**活动输入框行首**插入 `/name ` 字面量并把光标置于名称之后

- [ ] **Step 1: 落 CSS（规格 L73–L96）**

```css
/* 技能选择器（消息级）：与 .thinking-chip 同构，位于深度思考 chip 右侧 */
.skill-chip{display:inline-flex;align-items:center;gap:5px;height:28px;padding:0 12px;border:1px solid transparent;border-radius:14px;background:transparent;color:var(--text-secondary);font-size:12px;font-weight:500;flex-shrink:0;cursor:pointer;transition:background 150ms,color 150ms,border-color 150ms}
.skill-chip:hover{background:var(--bg)}
.skill-chip.active{background:var(--primary-light);border-color:var(--primary);color:var(--primary)}
.skill-chip:focus-visible{box-shadow:0 0 0 3px rgba(59,130,246,0.3)}
.skill-chip svg{width:13px;height:13px}
.skill-chip .chevron{width:10px;height:10px;opacity:.6}
.skill-menu{position:absolute;bottom:calc(100% + 8px);left:0;min-width:300px;max-height:320px;overflow-y:auto;background:var(--surface);border:1px solid var(--border);border-radius:12px;box-shadow:var(--shadow-lg);z-index:30;display:none}
.skill-menu.open{display:block}
```
（`.skill-menu` 的**向上弹出**是本任务的可判别点：锚点用 `bottom: calc(100% + 8px)`。）

- [ ] **Step 2: 落 HTML（两个 composer 各一处：`chat.html:743-756` 与 `783-802`）**

在各自 `composer-bottom` 内、`thinking-chip` **之后**插入（id 前缀区分 `new` / `history`）：

```html
              <button type="button" id="newSkillChip" class="skill-chip" aria-haspopup="listbox" aria-expanded="false" title="选择技能：插入 /技能名 后发送，本会话持续生效">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 5h7a4 4 0 0 1 4 4v10"/><path d="M21 5h-7a4 4 0 0 0-4 4v10"/></svg>
                <span id="newSkillLabel">技能</span>
                <svg class="chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg>
              </button>
              <div class="skill-menu" id="newSkillMenu" role="listbox" aria-label="选择技能">
                <div class="agent-menu-title">选择技能</div>
                <div id="newSkillList"></div>
                <div class="agent-menu-foot">插入 /名称 后发送，本会话持续生效</div>
              </div>
```

> `composer-bottom` 左侧当前只有 `thinking-chip`，技能 chip 紧随其后（间距由既有 `gap:10px` 提供；规格要求左侧组 `gap:6px`——**若与既有 10px 冲突，以既有值为准并在报告里说明**，视觉差异需在 `playwright-cli` 对照时确认可接受）。

- [ ] **Step 3: 落 JS**

```js
// 加载技能候选：首项为「不使用技能」（value=''）
async function loadSkills(){
  const fallback = [{ name:'', description:'不使用技能' }];
  try{
    const resp = await fetch('/api/skills', { headers:{ 'X-Trace-ID': generateTraceId() } });
    const body = await resp.json();
    const list = (body && body.data && Array.isArray(body.data.skills)) ? body.data.skills : [];
    state.skills = fallback.concat(list);
  }catch(e){ state.skills = fallback; }
  renderSkillMenu();
}

// 向活动输入框行首插入 `/name `（已有内容时插入到最前并补一个空格，光标置于名称之后）
function insertSkillPrefix(name){
  const c = activeComposer();
  const input = c.input;
  const text = input.value || '';
  const prefix = '/' + name + ' ';
  if(text.length === 0){
    input.value = prefix;
  }else if(text.charAt(0) === '/'){
    input.value = prefix + text.replace(/^\S+\s*/, '');
  }else{
    input.value = prefix + text;
  }
  input.focus();
  input.setSelectionRange(prefix.length, prefix.length);
  input.dispatchEvent(new Event('input'));
}

// 选中技能：单选、更新 chip 文案与选中态、向输入框插入前缀
function selectSkill(name){
  state.skill = name || '';
  paintSkillChip();
  renderSkillMenu();
  closeSkillMenu();
  if(state.skill){ insertSkillPrefix(state.skill); }
}
```
（`paintSkillChip()` 同时处理 `new`/`history` 两个 chip：空闲文案「技能」、激活文案 `技能 · <name>`、`.active` class 与 `aria-expanded`。）

- [ ] **Step 4: 接线**：`boot()` 调 `loadSkills();`；两个 chip 的 click/keydown 开合；click-outside 关闭；`newSession()` 重置 `state.skill=''` 并 `paintSkillChip()`。
- [ ] **Step 5: playwright-cli 验证**：菜单**向上**弹出（`bottom: calc(100%+8px)`）、首项「不使用技能」、选中后输入框行首出现 `/name ` 且 chip 变蓝显示技能名、再点可换；历史对话页同样有该 chip。
- [ ] **Step 6: 提交**

```bash
git add deploy/nginx/html/chat.html
git commit -m "feat(ui): 技能选择器 chip（向上弹菜单、选中插入 /name）"
```

---

### Task 3: 输入框 `/` 命令补全（与技能 chip 共用候选）

**Files:**
- Modify: `deploy/nginx/html/chat.html`（CSS：`.slash-menu`；JS：`onInputSlash()` / `renderSlashMenu()` / `applySlash()`）

**Interfaces:**
- Consumes：`state.skills`
- Produces：`onInputSlash(input)`（绑定到既有 `input` 事件之后——**不要覆盖既有 `sendBtn.disabled` 逻辑**）

**规则**：仅在**行首**触发（`value[0] === '/'`），按其后已输入片段按 `name` 前缀过滤；↑/↓ 移动、Enter 选中（**拦截发送**）、Esc 关闭；插入 `/name ` 字面量后关闭；`user-invocable:false` 的已由服务端过滤。

- [ ] **Step 1: 落 CSS + HTML**：在活动 composer 容器内加 `<div class="slash-menu" id="newSlashMenu" role="listbox"></div>`（history 同理），样式复用 `.skill-menu` 的外观但改为**向下**（输入框在上方）：`top:calc(100% + 6px)`。
- [ ] **Step 2: 落 JS**：`onInputSlash` 在 `initComposerEvents` 的既有 `input` 监听**之后**追加调用；`keydown` 里 Enter 分支：若 slash 菜单开着则**先** `applySlash()` 并 `preventDefault()`，否则走既有 `sendMessage()`。
- [ ] **Step 3: playwright-cli 验证**：行首输入 `/fin` → 菜单列出 `finance-analyst` 等；Enter 插入 `/finance-analyst ` 且**不发送**；Esc 关闭；`/api/v1` 这类非 slug 不触发。
- [ ] **Step 4: 提交**

```bash
git add deploy/nginx/html/chat.html
git commit -m "feat(ui): 输入框行首 / 命令补全（与技能 chip 共用候选）"
```

---

### Task 4: 请求体携带 `agent` + `agent_used` 静默纠正 + 历史页顶栏只读回显

**Files:**
- Modify: `deploy/nginx/html/chat.html`（`state.agent` 来源、`startStream` body、`buildStreamHandlers` 加 `agent_used`、`updateHeader` / `syncHeaderFromSession`、历史页禁用胶囊）

**Interfaces:**
- Consumes：`sessions/list` 项的新字段 `agent`；流事件 `agent_used`（`{agent: string}`）
- Produces：`state.boundAgent: string`（本会话**已绑定**值，来自会话记录）、`paintHeaderAgent(name)`

**要点**：请求体里的 `agent` 必须取**会话绑定值**（新会话取选择器 `state.agent`；恢复会话取 `sessions/list` 项的 `agent`），不是当轮 UI 状态；收到 `agent_used` 与显示不一致时**静默纠正**（不阻断、无 400 分支）。

- [ ] **Step 1: 请求体**

`startStream`（`:2317-2322`）的 body 补一个字段：

```js
    body: JSON.stringify({
      session_id: state.sessionId,
      kb_id: state.kbId,
      query: query,
      deep_thinking: state.deepThinking,
      agent: state.boundAgent || state.agent,   // 恢复会话用绑定值；新会话用选择器值
    }),
```

`state` 加 `boundAgent: ''`（会话绑定值）。`newSession()` 清 `state.boundAgent=''`；`syncHeaderFromSession()` 从 `sessionMap[sessionId]` 取 `info.agent` 写入 `state.boundAgent`。

- [ ] **Step 2: `agent_used` handler**

`buildStreamHandlers()`（`:2086-2238`，照 `model_info` 在 `:2160` 的写法）加：

```js
    agent_used(data){
      const used = data && data.agent ? data.agent : '';
      state.boundAgent = used;
      paintHeaderAgent(used);
    },
```

- [ ] **Step 3: 顶栏只读回显**

`updateHeader(title, kbName)`（`:974-977`）改为三段拼接：`会话名` + `知识库:XXX` + （非空时）`智能体:<display_name or name>`，其中智能体段用 `--primary`。`paintHeaderAgent(name)` 单独负责该段的显隐与文案（未选定不显示）。历史对话页**不渲染智能体胶囊**（`showHistoryPage()` 内 `closeAgentMenu()`；胶囊本身只在 `#page-new` 内，天然不显示）。

- [ ] **Step 4: playwright-cli 验证**：新会话选「财务专家」→ 发一轮 → 请求体含 `agent=finance-expert`；切到历史 → 顶栏显示 `智能体:财务专家` 且**无**胶囊；用 `playwright-cli requests` 核对请求体字段。
- [ ] **Step 5: 提交**

```bash
git add deploy/nginx/html/chat.html
git commit -m "feat(ui): 请求体携带会话绑定 agent + agent_used 静默纠正 + 顶栏只读回显"
```

---

### Task 5: 无障碍与互斥

**Files:**
- Modify: `deploy/nginx/html/chat.html`

- [ ] **Step 1: 补齐 aria 与键盘**：两个触发胶囊 `role="button"` / `aria-haspopup="listbox"` / `aria-expanded` / `tabindex="0"`；两个菜单 `role="listbox"` + 项 `role="option"` + `aria-selected`；Enter/Space 开合、↑/↓ 移动高亮（`aria-activedescendant` 可选）、Enter 选中、Esc 关闭并**把焦点归还触发胶囊**。
- [ ] **Step 2: 互斥**：扩展 `closeAllDrawers()`（`:1548-1551`）→ 打开引用抽屉 / 任务面板时先关两个新菜单；打开任一新菜单时先 `closeCiteDrawer()` + `closeTaskBoard()`。Esc 的既有处理（`:2929-2938`）补"关新菜单"。
- [ ] **Step 3: playwright-cli 验证**：键盘全流程（Tab 到胶囊 → Enter 开 → ↓ 移动 → Enter 选 → Esc 关且焦点回胶囊）；焦点环可见；`prefers-reduced-motion` 下无过渡动画；打开智能体菜单后打开引用抽屉 → 菜单自动关闭。
- [ ] **Step 4: 提交**

```bash
git add deploy/nginx/html/chat.html
git commit -m "feat(ui): 两个选择器的无障碍与面板互斥单开"
```

---

### Task 6: 设计文档同步 + playwright 端到端验证（§6.7）

**Files:**
- Modify: `docs/design/pages/chat-agent-skill-selector-2026-09-11.md`（补「验收 / 验证」小节 + 与实现不一致处回改）
- Modify: `docs/design/MASTER.md`（核对 L52–L53 两条组件登记行与 L66 页面行是否与实现一致，不一致则回改）

- [ ] **Step 1: 按 `ui-design-flow.md` 防腐**：核对规格与实际实现（类名、间距、文案、弹向、锁定文案），不一致处以**规格为准回改实现**；若确有必要改规格，则在规格里写明并同步 MASTER 的「关键规格」列。
- [ ] **Step 2: 给规格补「验收 / 验证」小节**（照 `docs/design/pages/chat-harness.md:199-206` 的格式）：列出 playwright-cli 核对项（两个选择器落位/默认项/弹向/插入字面量/顶栏回显/375px 不换行/键盘与互斥）。
- [ ] **Step 3: playwright-cli 端到端冒烟**（**登录后走真实后端**）：
```
playwright-cli open http://localhost/  → 登录 → chat.html
# 1) 智能体：选「财务专家」→ 发“帮我分析一下腾讯2024年报” → 断言顶栏智能体回显 + 请求体 agent
# 2) 技能：chip 选一个技能（若 `/api/skills` 暂无候选，则手输 `/finance-analyst 任务` 验证补全与插入）
# 3) 常规回归：不选智能体/技能发一轮普通提问 → token 流式、citation、done 正常
playwright-cli console   # 断言无新增 error
```
- [ ] **Step 4: 提交**

```bash
git add deploy/nginx/html/chat.html docs/design/pages/chat-agent-skill-selector-2026-09-11.md docs/design/MASTER.md
git commit -m "docs(design): 同步 chat-agent-skill-selector 规格与 MASTER，补验证小节"
```

---

## Self-Review

**1. Spec coverage（对照 change §6.1–6.7）**

| change 任务 | 覆盖 |
|---|---|
| 6.1 智能体选择器（新建页 + 默认项 + 菜单 + 锁提示） | T1 |
| 6.2 技能选择器（chip + 向上菜单 + 插入 `/name `） | T2 |
| 6.3 输入框 `/` 补全 | T3 |
| 6.4 会话内禁用更换 + 历史页顶栏只读回显 | T1 Step 4（新会话重置）+ T4 Step 3 |
| 6.5 请求体携带 `agent`（取绑定值）+ `agent_used` 纠正 | T4 |
| 6.6 无障碍与互斥 | T5 |
| 6.7 同步 MASTER/规格 + playwright 验证 | T6 |

**2. Placeholder scan**：T1/T2/T4 的 CSS/HTML/JS 已给出可直接落地的代码块；T3/T5 给出规则 + 落点 + 验证方式（**未逐行贴码**——`/` 补全的键盘状态机与无障碍的 `aria-activedescendant` 细节需执行时按规格 §无障碍 补全）；T6 为文档与验证步骤。**执行到 T3/T5 前须先补齐代码块**（与 Plan 2/Plan 3 相同处理）。

**3. Type consistency**：`state.agent`（选择器值，T1 定 → T4 消费）/ `state.boundAgent`（会话绑定值，T4 定）/ `state.skills`（T2 定 → T3 消费）/ `state.skill`（T2 定）/ `selectAgent(name)`（T1）/ `selectSkill(name)` + `insertSkillPrefix(name)`（T2）/ `onInputSlash(input)` + `applySlash()`（T3）/ `paintSkillChip()`（T2 定 → T4 复用）/ `paintHeaderAgent(name)`（T4）。命名已核对一致。

**4. 依赖与前置**
- **强前置（Plan 3）**：T1 依赖 `GET /api/agents`（Plan 3 T9）；T2/T3 依赖 `GET /api/skills`（Plan 3 T9）；T4 依赖请求体 `agent` 字段（Plan 3 T3）与 `agent_used` 事件（Plan 3 T3）、`sessions/list` 的 `agent`（Plan 3 T1）。**若接口未就绪，T1/T2 会静默降级为"仅默认项/仅不使用技能"**——因此 **Plan 4 必须在 Plan 3 之后执行**。
- **执行方式**：每个任务的实现**先调用 `frontend-design` skill**（规格为唯一视觉依据、mockup 仅作参考），改完用 `playwright-cli` 对照设计稿验证。
- **不在本计划内**：`login.html` / `index.html` 的改动；后端任何改动。
