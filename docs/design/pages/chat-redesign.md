# Chat 页面重设计 — 会话历史侧边栏 + 本次需求

基于 `d37b0dc`（composer 化版本）重设计 chat.html：**恢复会话历史侧边栏**（旧版 67f908d 的 sidebar 在 3e5df78 重写时被移除），同时**保留本次全部需求改动**（深度思考 / composer 澄清 / abstention / 反馈 / 引用 / 模型信息 / 状态标签）。视觉基线遵循 `docs/design/MASTER.md`。

## 布局决策

```
┌─ Sidebar (240px, #0F172A) ─┬─ Chat Main (flex) ────────────────┐
│ Logo: [FQ] Corporate RAG    │ Header: 企业知识库助手 · 会话 ID   │
│ 导航: 💬 智能问答           │ ┌─ 消息流（全部现有组件）────────┐  │
│       📚 知识库管理         │ │ 状态/气泡/引用/反馈/abstention  │  │
│ ────────────────────────── │ └──────────────────────────────┘  │
│ 会话历史 [＋ 新建]          │ Footer: [深度思考] [输入] [➤]     │
│  · 会话项（可滚动）         │                                   │
└────────────────────────────┴───────────────────────────────────┘
```

- **侧边栏深色**（`#0F172A`）与 `index.html` 一致，系统内统一
- **主聊天区**保留现有 Minimalism & Swiss 白底风格（`--bg #F8FAFC`）
- 布局：`display: flex`，sidebar `240px` 固定 + chat-main `flex: 1` 自适应

## 侧边栏规格

| 区块 | 规格 |
|------|------|
| Logo | `[FQ]` 徽标（30×30 primary 圆角）+ "Corporate RAG" + "金融文档智能问答系统"（11px muted） |
| 导航 | 两栏：💬 智能问答（`/`，active 态 `--sidebar-active` 蓝底 + `#60A5FA`）、📚 知识库管理（`/Knowledgebase`）；hover 深色块 |
| 会话历史 | 标题（11px uppercase）+ 新建会话按钮（26×26，hover primary）；列表项 13px 圆角，active 蓝底，hover 浅深色，单行省略 |
| 数据源 | `POST /api/sessions/list`（按当前用户）加载会话列表；新建会话生成新 session_id |

## 保留的本次组件（不改动）

深度思考开关（chip，选中 ✓）、ask_user 澄清 composer（**输入区接管，不进消息流**）、abstention 转人工（amber 条 + 橙按钮）、反馈（SVG 👍/👎）、引用卡片、模型信息（fallback 徽标）、状态标签（呼吸圆点）——全部规格见 `pages/agentic-clarification.md`，此处不重复。

## 右上角 User Area

chat-header 右侧（`margin-left: auto` 右对齐）常驻用户区：

| 状态 | 内容 |
|------|------|
| 登录 | 圆形头像按钮（30×30，primary→indigo 渐变），点击展开下拉菜单 →"退出登录"（调 `POST /api/auth/logout` + 清 cookie + 跳 `/login.html`） |
| 未登录 | "登录"按钮（渐变 primary 底白字），链接 `/login.html?redirect=<当前路径>` |

- 登录态判定：`POST /api/auth/verify` 返回 `data.valid`；逻辑与 `index.html` 的 `updateUserArea()` 一致（同 `js/api.js`）
- 头像下拉含点击外部关闭（复用 index.html 的 `toggleUserDropdown` + backdrop 模式）

## 移动端（<768px）

- 侧边栏默认收起：`position: fixed; transform: translateX(-100%)`（复用旧版 `toggleSidebar()` + overlay + hamburger）
- 主区占满全宽；输入区 `flex-wrap: wrap` 保证深度思考 chip 换行不挤压输入框
- 会话历史在抽屉内可滚动

## 验证

改完 `playwright-cli` 打开 `http://localhost/` 验证：侧边栏会话列表加载、切换会话、新建会话、移动端抽屉；既有聊天功能不回归。
