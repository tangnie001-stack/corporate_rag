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
- 注释一律**中文**；新增 CSS 变量一律复用既有变量（`--primary` / `--primary-light` / `--surface` / `--border` / `--bg` / `--text` / `--text-secondary` / `--text-muted` / `--shadow` / `--shadow-lg`），**不新增颜色字面量**。唯一例外是焦点环 `box-shadow: 0 0 0 3px rgba(59,130,246,0.3)` —— **逐字沿用** `.thinking-chip:focus-visible`（`chat.html:237`）的既有写法；若既有样式表已提供可复用的焦点类，优先复用该类而不重复声明。
- **前端 JS 沿用 `chat.html` 自身风格**：该文件大量使用三元表达式与 `escapeHtml()`（如 `renderKbMenu` `:2491-2501`、`renderModelInfo` `:1643-1652`）。CLAUDE.md 的「不用三元表达式」是针对 Python 的语法约束，**不适用于此文件**；判断标准是"与文件既有写法一致"。转义一律走既有 `escapeHtml()`，**不得**把未转义的用户/配置文本拼进 `innerHTML`。
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
      + '<span class="check">' + (selected ? '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3"><path d="M20 6L9 17l-5-5"/></svg>' : '') + '</span></div>';
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

> 规格已按实际视觉改定：`composer-bottom` 左侧组间距 **10px**（原规格写 6px，与既有 `.composer-bottom{gap:10px}` 冲突；已把规格那一处改成 10px，`chat-agent-skill-selector-2026-09-11.md` L77）。因此技能 chip **只需紧随 `thinking-chip` 之后**，不需要包一层容器，也不新增 gap 声明。

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
// 渲染技能菜单：首项「不使用技能」+ 真实技能；候选项带 data-name 供键盘选中
// （option 的 id 与 aria-activedescendant 由 T5 的 paintMenuNav 运行时写入，此处不写 id）
function renderSkillMenu(idPrefix){
  const box = $(idPrefix + 'List');
  if(!box) return;
  const hasReal = state.skills.some(function(s){ return !!s.name; });  // 是否有真实技能
  const current = state.skill || '';
  box.innerHTML = state.skills.map(function(s){
    const selected = (s.name === current);
    const label = s.name ? ('/' + s.name) : '不使用技能';
    const desc = s.name ? (s.description || '') : '';
    return '<div class="agent-item" role="option" tabindex="-1" aria-selected="' + (selected ? 'true' : 'false') + '" data-name="' + escapeHtml(s.name) + '">'
      + '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:13px;height:13px"><path d="M3 5h7a4 4 0 0 1 4 4v10"/><path d="M21 5h-7a4 4 0 0 0-4 4v10"/></svg>'
      + '<span class="agent-item-info"><span class="agent-item-name">' + escapeHtml(label) + '</span>'
      + (desc ? '<span class="agent-item-desc">' + escapeHtml(desc) + '</span>' : '') + '</span>'
      + '<span class="check">' + (selected ? '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3"><path d="M20 6L9 17l-5-5"/></svg>' : '') + '</span>'
      + '</div>';
  }).join('') + (hasReal ? '' : '<div class="slash-empty">暂无可调用技能</div>');   // 空态提示（规格 chat-harness-ui）
  Array.prototype.forEach.call(box.querySelectorAll('.agent-item'), function(el){
    el.addEventListener('mousedown', function(ev){
      ev.preventDefault();                       // 保焦点，避免输入框失焦
      selectSkill(el.getAttribute('data-name'));
    });
  });
}

// 开合与 chip 状态：idPrefix ∈ {'newSkill','historySkill'}（两个 chip 共用同一份 state.skill）
function openSkillMenu(idPrefix){
  closeTransientOverlays();                      // 互斥（T5）
  $(idPrefix + 'Menu').classList.add('open');
  $(idPrefix === 'newSkill' ? 'newSkillChip' : 'historySkillChip').setAttribute('aria-expanded', 'true');
  renderSkillMenu(idPrefix);
}
function closeSkillMenu(){
  ['newSkillMenu', 'historySkillMenu'].forEach(function(id){
    const menu = $(id);
    if(menu){ menu.classList.remove('open'); }
  });
  $('newSkillChip').setAttribute('aria-expanded', 'false');
  $('historySkillChip').setAttribute('aria-expanded', 'false');
}

// chip 文案与选中态（两个 chip 同步显示同一 state.skill）
function paintSkillChip(){
  const label = state.skill ? ('技能 · ' + state.skill) : '技能';
  ['newSkillChip', 'historySkillChip'].forEach(function(id){
    const chip = $(id);
    if(!chip) return;
    chip.classList.toggle('active', !!state.skill);
    chip.querySelector('span').textContent = label;
  });
}
```

- [ ] **Step 4: 接线**：`boot()` 调 `loadSkills();`；两个 chip 的 click 开合（T5 补键盘）；click-outside 关闭（在既有 `document` click 处理里补 `closeSkillMenu()`）；`newSession()` 重置 `state.skill=''` 并 `paintSkillChip()`。
- [ ] **Step 5: playwright-cli 验证**：菜单**向上**弹出（`bottom: calc(100%+8px)`）、首项「不使用技能」、选中后输入框行首出现 `/name ` 且 chip 变蓝显示技能名、再点可换；历史对话页同样有该 chip。
- [ ] **Step 6: 提交**

```bash
git add deploy/nginx/html/chat.html
git commit -m "feat(ui): 技能选择器 chip（向上弹菜单、选中插入 /name）"
```

---

### Task 3: 输入框 `/` 命令补全（与技能 chip 共用候选）

**Files:**
- Modify: `deploy/nginx/html/chat.html`（CSS：`.composer-container` 补 `position:relative`、新增 `.slash-*`；HTML：两个 composer 各加一个菜单容器；JS：`slashState` + `onInputSlash` / `slashMatches` / `renderSlashMenu` / `applySlash` / `handleSlashKeydown`；改造既有 `input` / `keydown` 监听）

**Interfaces:**
- Consumes：`state.skills`（T2 已加载，含前端合成的 `{name:'', description:'不使用技能'}` 首项）、既有 `escapeHtml()`、`activeComposer()`
- Produces：
  - `slashState = { open: bool, frag: string, matches: Array, active: int }`
  - `onInputSlash(input)` / `slashMatches(frag)` / `renderSlashMenu()` / `applySlash(name)` / `handleSlashKeydown(e) -> bool`（**返回 true = 已消费按键**）

**交互规则（已定，逐条实现）**
1. **触发**：仅输入框行首命中 `/^\/([A-Za-z0-9_-]*)$/`（与后端 `PREFIX_PATTERN` 的字符集**同源**）→ 开菜单；不命中（含 `/api/v1` 这类带 `/` 的路径、行内 `/`、`/` 后跟空格）→ 关菜单。
2. **候选**：只取**真实技能**（`s.name` 非空，**排除**「不使用技能」首项），按 `name` **前缀**匹配 `frag`，按 `name` 字典序排序。
3. **键盘**：↑/↓ 在候选内移动且**到边界即停（不环绕）**；Enter 有高亮项 → 插入不发送，**无匹配 → 关菜单且不发送**（防误发半截命令）；Esc → 关菜单（焦点留在输入框）。以上按键一律 `preventDefault()` 并由 `handleSlashKeydown` 返回 `true`。
4. **鼠标**：候选支持点击选中（用 `mousedown` + `preventDefault()`，避免 textarea 失焦）。
5. **插入**：把行首的 `/frag` 替换为 `/name `（保留其余文本），光标置于名称之后，并 `dispatchEvent(new Event('input'))` 以刷新发送键可用态。
6. 菜单 `max-height` 复用 `320px`（与 chip 菜单一致）；无匹配时显示一行「没有匹配的技能」。
7. `user-invocable:false` 的技能已由服务端过滤，前端不再判。

- [ ] **Step 1: 落 CSS**

```css
/* 输入框行首 / 命令补全：外观沿用技能菜单，改为向下弹出 */
.composer-container{position:relative}
.slash-menu{position:absolute;top:calc(100% + 6px);left:0;min-width:300px;max-height:320px;overflow-y:auto;background:var(--surface);border:1px solid var(--border);border-radius:12px;box-shadow:var(--shadow-lg);z-index:30;display:none}
.slash-menu.open{display:block}
.slash-item{display:flex;align-items:center;gap:10px;padding:9px 14px;cursor:pointer}
.slash-item:hover,.slash-item.active{background:var(--bg)}
.slash-item-name{font-size:13px;color:var(--text);font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.slash-item-desc{font-size:12px;color:var(--text-secondary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.slash-empty{padding:10px 14px;font-size:12px;color:var(--text-muted)}
@media (prefers-reduced-motion:reduce){.slash-menu{transition:none}}
```
> `.composer-container` 若已声明 `position`（先 grep 确认），**不要重复声明**。

- [ ] **Step 2: 落 HTML（两个 composer 各一处：`chat.html:740-757`、`783-802`）**

放在各自 `.composer-container` 内、`textarea` **之后**：

```html
              <div class="slash-menu" id="newSlashMenu" role="listbox" aria-label="技能候选"></div>
```

- [ ] **Step 3: 落 JS**

```js
// ===== 输入框行首 / 命令补全（与技能 chip 共用 state.skills）=====
let slashState = { open:false, frag:'', matches:[], active:0 };
const SLASH_FRAG_RE = /^\/([A-Za-z0-9_-]*)$/;   // 与后端 prefix.PREFIX_PATTERN 字符集同源

// 候选：排除「不使用技能」首项（name 为空），按 name 前缀匹配后字典序排序
function slashMatches(frag){
  return state.skills
    .filter(function(s){ return !!s.name && s.name.indexOf(frag) === 0; })
    .sort(function(a,b){ return a.name.localeCompare(b.name); });
}

function renderSlashMenu(){
  const c = activeComposer();
  const box = c.slashMenu;
  if(!box) return;
  if(!slashState.open){ box.classList.remove('open'); return; }
  if(slashState.matches.length === 0){
    box.innerHTML = '<div class="slash-empty">没有匹配的技能</div>';
    box.classList.add('open');
    c.input.removeAttribute('aria-activedescendant');
    return;
  }
  box.innerHTML = slashState.matches.map(function(s, i){
    const on = (i === slashState.active);
    return '<div class="slash-item' + (on ? ' active' : '') + '" id="' + c.idPrefix + 'SlashOpt' + i + '"'
      + ' role="option" aria-selected="' + (on ? 'true' : 'false') + '" data-name="' + escapeHtml(s.name) + '">'
      + '<span class="slash-item-name">/' + escapeHtml(s.name) + '</span>'
      + (s.description ? '<span class="slash-item-desc">' + escapeHtml(s.description) + '</span>' : '')
      + '</div>';
  }).join('');
  box.classList.add('open');
  // 焦点留在输入框，用 aria-activedescendant 播报高亮项
  c.input.setAttribute('aria-activedescendant', c.idPrefix + 'SlashOpt' + slashState.active);
  Array.prototype.forEach.call(box.querySelectorAll('.slash-item'), function(el){
    el.addEventListener('mousedown', function(ev){
      ev.preventDefault();                       // 保焦点，避免 textarea 失焦
      applySlash(el.getAttribute('data-name'));
    });
  });
}

function onInputSlash(input){
  const m = SLASH_FRAG_RE.exec(input.value || '');
  if(!m){
    slashState.open = false;
    slashState.matches = [];
    renderSlashMenu();
    return;
  }
  slashState.open = true;
  slashState.frag = m[1];
  slashState.matches = slashMatches(slashState.frag);
  slashState.active = 0;
  renderSlashMenu();
}

// 插入选中技能：行首 /frag → /name␣（保留其余文本），光标置于名称之后
function applySlash(name){
  const c = activeComposer();
  const rest = (c.input.value || '').slice(slashState.frag.length + 1);  // +1 = 前导 '/'
  const prefix = '/' + name + ' ';
  c.input.value = prefix + rest;
  c.input.focus();
  c.input.setSelectionRange(prefix.length, prefix.length);
  c.input.dispatchEvent(new Event('input'));
  slashState.open = false;
  slashState.matches = [];
  renderSlashMenu();
}

// 键盘：返回 true = 已消费（调用方不得再走发送逻辑）
function handleSlashKeydown(e){
  if(!slashState.open) return false;
  if(e.key === 'Escape'){
    e.preventDefault();
    slashState.open = false;
    renderSlashMenu();
    return true;
  }
  if(e.key === 'ArrowDown' || e.key === 'ArrowUp'){
    e.preventDefault();
    if(slashState.matches.length){
      if(e.key === 'ArrowDown' && slashState.active < slashState.matches.length - 1){
        slashState.active += 1;
      }
      if(e.key === 'ArrowUp' && slashState.active > 0){
        slashState.active -= 1;
      }
      renderSlashMenu();
    }
    return true;
  }
  if(e.key === 'Enter'){
    e.preventDefault();
    if(slashState.matches.length === 0){
      slashState.open = false;                   // 无匹配：关菜单且不发送
      renderSlashMenu();
      return true;
    }
    applySlash(slashState.matches[slashState.active].name);
    return true;
  }
  return false;
}
```

- [ ] **Step 4: 改造既有监听（**唯一改动既有代码的地方**，保持 Enter 行为逐字不变）**

`initComposerEvents`（`chat.html:2432-2447`）里两处监听改为：

```js
  c.input.addEventListener('input', function(){
    onInputSlash(c.input);                       // 新增：行首 / 补全
    c.sendBtn.disabled = !c.input.value.trim();  // 既有逻辑原样保留
  });
  c.input.addEventListener('keydown', function(e){
    if(handleSlashKeydown(e)) return;            // 新增：补全优先消费按键
    if(e.key === 'Enter'){ e.preventDefault(); sendMessage(); }   // 既有逻辑原样保留
  });
```
并给 `composerNew()` / `composerHistory()` 返回的对象补 `slashMenu`（`$('newSlashMenu')` / `$('historySlashMenu')`）与 `idPrefix`（`'new'` / `'history'`）。`newSession()` 里补 `slashState.open = false;`。

- [ ] **Step 5: playwright-cli 验证（逐条对照交互规则）**

```
playwright-cli open http://localhost/chat.html   # 需登录（cookie token）
# ① 行首 /fin → 菜单列出 /finance-analyst（且不含「不使用技能」）
# ② Enter → 输入框变 "/finance-analyst " 且【未发送】（消息列表无新增）
# ③ Esc → 菜单关闭、焦点仍在输入框
# ④ /api/v1 与 "abc /fin" → 菜单不出现
# ⑤ /zzz → 显示「没有匹配的技能」，Enter 不发送
# ⑥ 鼠标点击候选项 → 正确插入且不发送
playwright-cli console   # 断言无新增 error
```

- [ ] **Step 6: 提交**

```bash
git add deploy/nginx/html/chat.html
git commit -m "feat(ui): 输入框行首 / 命令补全（与技能 chip 共用候选，Enter 不误发）"
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
- Modify: `deploy/nginx/html/chat.html`（新增 `closeTransientOverlays` / `menuNav` / `menuOptions` / `paintMenuNav` / `handleMenuKeydown`；改造 `openAgentMenu`/`openSkillMenu`、`openCiteDrawer`/`openTaskBoard`、既有 Esc 监听；给两个触发钮绑 click/keydown）

**Interfaces:**
- Consumes：T1 的 `agentTrigger`/`agentMenu`/`closeAgentMenu`/`selectAgent`；T2 的 `newSkillChip`/`newSkillMenu`/`historySkillChip`/`historySkillMenu`/`closeSkillMenu`/`selectSkill`；既有 `closeKbMenu()`（`:2508`）、`openCiteDrawer()`（`:1402`）、`openTaskBoard()`（`:1525`）、既有 Esc 监听（`:2929-2938`）
- Produces：`closeTransientOverlays()`；`handleMenuKeydown(kind, triggerId, menuId, idPrefix, e) -> bool`

**已定的无障碍与互斥规则**
1. ↑/↓ 在候选内移动、**到边界即停（不环绕）**。
2. **加 `aria-activedescendant`**：焦点留在触发钮，高亮项由该属性播报（`role="option"` + `aria-selected` 由规格 L121 要求；`aria-activedescendant` 是本实现补的，因为焦点不在选项上）。
3. **互斥扩大到全部"瞬时浮层"**：智能体菜单、技能菜单、KB 菜单、用户下拉四者**互斥单开**；打开任一新菜单前先关其余三个。引用抽屉 / 任务面板是"强浮层"，打开它们时也先关掉全部瞬时浮层。
4. **Esc 优先级**：先关"新菜单"（若有开），否则走既有抽屉/面板逻辑——**在同一个监听里加分支，不新增监听**（既有注释已写明"task-board 复用既有 keydown，不重复注册监听"）。
5. `prefers-reduced-motion` 下禁用两个新组件的全部 `transition`（T1/T2 的 media query 已覆盖，此处只需核对）。
6. 选项 id 前缀**必须唯一**：智能体菜单 `agentOpt{i}`、左侧技能菜单 `newSkillOpt{i}`、历史页技能菜单 `historySkillOpt{i}`。

- [ ] **Step 1: 落通用导航与互斥辅助函数**

```js
// ===== 无障碍与互斥（T5）：两个新菜单的通用键盘导航 + 瞬时浮层互斥 =====
const menuNav = { agent:{ active:-1 }, skill:{ active:-1 } };

// 统一关闭"瞬时浮层"：KB 菜单 / 智能体菜单 / 技能菜单 / 用户下拉。
// 不含引用抽屉与任务面板（"强浮层"，由 closeAllDrawers 管）。
function closeTransientOverlays(){
  closeKbMenu();
  closeAgentMenu();
  closeSkillMenu();
  const dd = document.getElementById('userDropdown');
  if(dd){ dd.classList.remove('show'); }
}

function menuOptions(menu){
  return Array.prototype.slice.call(menu.querySelectorAll('[role="option"]'));
}

// 候选 id + 高亮态（class / aria-selected / 触发钮的 aria-activedescendant 三处同步）
function paintMenuNav(kind, triggerId, menuId, idPrefix){
  const menu = $(menuId);
  const trigger = $(triggerId);
  if(!menu || !trigger) return;
  const items = menuOptions(menu);
  const active = menuNav[kind].active;
  items.forEach(function(el, i){
    const on = (i === active);
    el.id = idPrefix + 'Opt' + i;
    el.classList.toggle('active', on);
    el.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  if(active >= 0 && items[active]){
    trigger.setAttribute('aria-activedescendant', items[active].id);
  }else{
    trigger.removeAttribute('aria-activedescendant');
  }
}

// 通用键盘处理：返回 true = 已消费该按键
function handleMenuKeydown(kind, triggerId, menuId, idPrefix, e){
  const menu = $(menuId);
  if(!menu || !menu.classList.contains('open')) return false;
  const items = menuOptions(menu);
  if(e.key === 'Escape'){
    e.preventDefault();
    menuNav[kind].active = -1;
    if(kind === 'agent'){ closeAgentMenu(); } else { closeSkillMenu(); }
    $(triggerId).focus();                       // 关闭后焦点归还触发钮（规格 L122）
    return true;
  }
  if(e.key === 'ArrowDown' || e.key === 'ArrowUp'){
    e.preventDefault();
    if(items.length){
      if(e.key === 'ArrowDown' && menuNav[kind].active < items.length - 1){
        menuNav[kind].active += 1;
      }
      if(e.key === 'ArrowUp' && menuNav[kind].active > 0){
        menuNav[kind].active -= 1;
      }
      paintMenuNav(kind, triggerId, menuId, idPrefix);
    }
    return true;
  }
  if(e.key === 'Enter' || e.key === ' '){
    e.preventDefault();
    const active = menuNav[kind].active;
    if(active >= 0 && items[active]){
      const name = items[active].getAttribute('data-name') || '';
      if(kind === 'agent'){ selectAgent(name); } else { selectSkill(name); }
    }else if(kind === 'agent'){
      closeAgentMenu();
    }else{
      closeSkillMenu();
    }
    $(triggerId).focus();
    return true;
  }
  return false;
}
```

- [ ] **Step 2: 给两个触发钮绑 click + keydown，并在开菜单前互斥**

```js
  // 智能体胶囊
  $('agentTrigger').addEventListener('click', function(){
    if($('agentMenu').classList.contains('open')){ closeAgentMenu(); return; }
    openAgentMenu();
  });
  $('agentTrigger').addEventListener('keydown', function(e){
    if(handleMenuKeydown('agent', 'agentTrigger', 'agentMenu', 'agent', e)) return;
    if(e.key === 'Enter' || e.key === ' '){
      e.preventDefault();
      menuNav.agent.active = 0;                 // 键盘打开默认高亮首项（「默认」）
      openAgentMenu();
    }
  });

  // 技能 chip（new / history 各一份，id 前缀不同以免 option id 冲突）
  [['newSkillChip','newSkillMenu','newSkill'], ['historySkillChip','historySkillMenu','historySkill']]
    .forEach(function(trio){
      const triggerId = trio[0], menuId = trio[1], prefix = trio[2];
      $(triggerId).addEventListener('click', function(){
        if($(menuId).classList.contains('open')){ closeSkillMenu(); return; }
        openSkillMenu(prefix);
      });
      $(triggerId).addEventListener('keydown', function(e){
        if(handleMenuKeydown('skill', triggerId, menuId, prefix, e)) return;
        if(e.key === 'Enter' || e.key === ' '){
          e.preventDefault();
          menuNav.skill.active = 0;
          openSkillMenu(prefix);
        }
      });
    });
```
> **T2 已按本任务的要求实现**：`openSkillMenu(idPrefix)` 已接收前缀、`renderSkillMenu(idPrefix)` 已给候选项写 `data-name`、候选项复用 T1 的 `.agent-item` 样式（**不新增 `.skill-item`**）。本任务只补键盘与 aria。

- [ ] **Step 3: 互斥接线（**只加一行，不改既有逻辑**）**

- T1 的 `openAgentMenu` **首行**插入 `closeTransientOverlays();`（T2 的 `openSkillMenu` 已含该行）。
- `openCiteDrawer()`（`:1402`）与 `openTaskBoard()`（`:1525`）**首行**插入 `closeTransientOverlays();`（不要删掉它们彼此之间既有的 `closeTaskBoard()` / `closeCiteDrawer()` 那一行）。
- `closeAllDrawers()` 保持原样（它只管强浮层）。

- [ ] **Step 4: 扩展既有 Esc 监听（不新增监听）**

`chat.html:2929-2938` 的监听体改为：

```js
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    // 优先级：瞬时浮层（新菜单）→ 强浮层（引用抽屉 / 任务面板）
    if ($('agentMenu') && $('agentMenu').classList.contains('open')) {
      closeAgentMenu();
      $('agentTrigger').focus();
      return;
    }
    if ($('newSkillMenu') && $('newSkillMenu').classList.contains('open')) {
      closeSkillMenu();
      $('newSkillChip').focus();
      return;
    }
    if ($('historySkillMenu') && $('historySkillMenu').classList.contains('open')) {
      closeSkillMenu();
      $('historySkillChip').focus();
      return;
    }
    const drawer = document.getElementById('citeDrawer');
    if (drawer && drawer.classList.contains('open')) {
      closeCiteDrawer();
    } else if (taskBoard.open) {
      closeTaskBoard();
    }
  }
});
```

- [ ] **Step 5: 核对 aria 与 reduced-motion**

- `agentTrigger` 是 `<div>` → 必须显式 `role="button"`（T1 已含）；两个技能 chip 是 `<button>` → `role="button"` 已隐式，**不重复标注**。
- 两个新菜单 `role="listbox"` + `aria-label`（T1/T2 已含）；候选项 `role="option"` + `aria-selected`（由 `paintMenuNav` 运行时写入）。
- 确认 T1/T2 的 `@media (prefers-reduced-motion:reduce)` 覆盖了 `.agent-trigger` / `.skill-chip` / `.agent-menu` / `.skill-menu`。

- [ ] **Step 6: playwright-cli 验证（键盘全流程 + 互斥 + 焦点）**

```
playwright-cli open http://localhost/chat.html   # 需登录
# ① Tab 到智能体胶囊 → Enter 开（首项「默认」高亮）→ ↓ ↓ → Enter 选中 → 胶囊文案变化
# ② Enter 再开 → Esc → 菜单关闭且【焦点回到胶囊】（eval 断言 document.activeElement.id）
# ③ 打开智能体菜单后点引用抽屉 → 智能体菜单自动关闭（互斥）
# ④ 打开 KB 菜单后点智能体胶囊 → KB 菜单自动关闭；反向同理
# ⑤ 键盘走一遍技能 chip（new 与 history 各一次），确认高亮与选中正确、无 id 冲突
# ⑥ 系统开启"减弱动态效果"或模拟 prefers-reduced-motion → 无过渡动画
playwright-cli console   # 断言无新增 error
```

- [ ] **Step 7: 提交**

```bash
git add deploy/nginx/html/chat.html
git commit -m "feat(ui): 两个选择器的无障碍（键盘 + aria-activedescendant + 焦点归还）与浮层互斥单开"
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

**2. Placeholder scan**：**T1–T5 全部满配**（T1/T2/T4 给出可直接落地的 CSS/HTML/JS；T3 给出完整补全状态机与既有监听的改造前后对比；T5 给出通用导航/互斥辅助函数、两个触发钮的绑定、Esc 监听改造与 aria 核对清单）。T6 是文档同步 + playwright 端到端验证（非代码任务，已给出逐条验证项与提交清单）。已知的**刻意留白**仅一处：T4 的 `get_session_agent` 读取方式与 Plan 3 一致（见 Plan 3 T3 的二选一），前端不涉及。

**3. Type consistency**：`state.agent`（选择器值，T1 定 → T4 消费）/ `state.boundAgent`（会话绑定值，T4 定）/ `state.skills`（T2 定 → T3 消费）/ `state.skill`（T2 定）/ `selectAgent(name)`（T1）/ `renderAgentMenu()` + `openAgentMenu()` + `closeAgentMenu()`（T1 定 → T5 消费）/ `renderSkillMenu(idPrefix)` + `openSkillMenu(idPrefix)` + `closeSkillMenu()` + `selectSkill(name)` + `insertSkillPrefix(name)` + `paintSkillChip()`（T2 定 → T4/T5 消费）/ `slashState` + `slashMatches(frag)` + `renderSlashMenu()` + `applySlash(name)` + `onInputSlash(input)` + `handleSlashKeydown(e)`（T3 定）/ `closeTransientOverlays()` + `menuNav` + `menuOptions(menu)` + `paintMenuNav(kind, triggerId, menuId, idPrefix)` + `handleMenuKeydown(kind, triggerId, menuId, idPrefix, e)`（T5 定）/ `paintHeaderAgent(name)`（T4 定）。DOM id：`agentTrigger`/`agentMenu`/`agentList`/`agentLabel`（T1）、`newSkillChip`/`newSkillMenu`/`newSkillList`/`historySkillChip`/`historySkillMenu`/`historySkillList`（T2）、`newSlashMenu`/`historySlashMenu`（T3）；option id 前缀 `agent` / `newSkill` / `historySkill`。命名与 id 已核对一致。

**4. 依赖与前置**
- **强前置（Plan 3）**：T1 依赖 `GET /api/agents`（Plan 3 T9）；T2/T3 依赖 `GET /api/skills`（Plan 3 T9）；T4 依赖请求体 `agent` 字段（Plan 3 T3）与 `agent_used` 事件（Plan 3 T3）、`sessions/list` 的 `agent`（Plan 3 T1）。**若接口未就绪，T1/T2 会静默降级为"仅默认项/仅不使用技能"**——因此 **Plan 4 必须在 Plan 3 之后执行**。
- **执行方式**：每个任务的实现**先调用 `frontend-design` skill**（规格为唯一视觉依据、mockup 仅作参考），改完用 `playwright-cli` 对照设计稿验证。
- **已知并接受的延后（§5.14，与 Plan 3 同一决定）**：生产 `agents/`/`skills/` 未挂载未 COPY。**本轮只在开发环境跑通**（dev override 已挂两个目录）；上生产前必须补 prod 挂载或 Dockerfile COPY —— 具体见 Plan 3 Self-Review §4，本计划不重复。
- **"开发环境跑通"的验收标准**：见 Plan 3 Self-Review §4 的 6 条；其中第 5（chip 插入 + `/` 补全）、第 6（常规轮回归）由本计划的 T6 负责走完，第 1/2/3/4 条跨 Plan 3+4。
- **不在本计划内**：`login.html` / `index.html` 的改动；后端任何改动；生产部署改动。
