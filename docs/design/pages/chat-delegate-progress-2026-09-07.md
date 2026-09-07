# Chat：子代理过程区 + 任务/进度看板 — 前端设计

对应三个 openspec change 的前端改动：
- `delegate-hardening-observability`（core）：回答内「领域专家分析过程」折叠区 + 委派终态文案（完成/中断）
- `task-board`：任务/进度看板（chip + 右侧只读面板 + 快照加载）
- `sse-stream-resilience`：无新增视觉（仅 `onClose`→resume 行为，复用现有断线续接/提示）

视觉基线遵循 `docs/design/MASTER.md`（Minimalism & Swiss、primary `#3B82F6` / accent `#F97316`、720px 消息列、Lexend + Source Sans 3、150ms 过渡）。图标一律 SVG（不用 emoji）。

## 组件清单

| 组件 | 归属 change | 触发 | 说明 |
|------|------------|------|------|
| 分析过程折叠区 delegate-progress | core | `delegate` SSE 事件（kind=thinking/content） | 嵌入**该条 AI 回答气泡**内；按 delegate_id 分节 |
| 委派状态文案 | core | `delegate` end（ok/reason） | "领域专家分析完成" / "分析中断（原因）" |
| 任务/进度看板 task-board | task-board | `task` SSE 事件 + 快照接口 | 头部 chip → 右侧只读面板，plan/execution 分组 |

## 组件规格

### 1. 分析过程折叠区 delegate-progress（core）

**位置**：AI 回答气泡内 `bubble-content` 之后、引用来源横条/反馈之前；整区仍属该条消息的 DOM 子块，`overflow` 不影响回答正文。

- **结构**（每个 delegate_id 一节）：
  - 节头（可点击整行，`cursor-pointer`，`aria-expanded`）：`[技能名] 领域专家分析` 标题 + 左侧 6px 状态圆点（运行中 primary 呼吸 / 完成 text-secondary / 中断 error）+ 右侧 chevron SVG + 行尾状态文案
  - 节体（展开默认，运行中自动跟随；结束时保持用户选择）：
    - **思考过程** 二级折叠：`reasoning` 增量，样式 muted、字号 13px、行高 1.6、左边 3px primary-light 条；二级头 "思考过程" + 字数（`reasoning` 增量过长时以省略风险提示）
    - **分析正文**：`content` 增量流式，正常正文排版（16px/1.5），白底正文区与思考区分隔
- **样式**：节体容器 `--surface-muted` 底、1px `--border`、圆角 10px、内边距 12px；节头与节体间隙 8px；节间分隔 16px
- **多 delegate**：同一气泡内按 delegate_id 分节、可分别折叠；顺序与事件到达一致
- **状态与文案**：运行中节头圆点 primary + 文案"正在分析…"；正常结束圆点 text-secondary + "领域专家分析完成"；中断（idle/total/turn/failed）圆点 error + **"分析中断（原因）"**（原因中文短词：空闲超时 / 超时 / 轮次上限 / 失败），行尾可用 11px muted 注明
- **渲染**：`delegate` 事件（kind=thinking|content、delegate_id、delta）按 id 找到节；无 id 对应节则丢弃并 warn；thinking 与 content 各自累积后流式追加；运行中节自动滚动到底（`scrollIntoView` 若该气泡可见）
- **动画**：展开/收起 150-200ms；呼吸点 opacity 1↔0.35（1.2s）；`prefers-reduced-motion` 时停用呼吸/滚动动画
- **持久化**：过程区仅运行期/缓冲回放展示，历史重载不重建（D7），故无需存储层配合

**无障碍**：节头为 `button` 语义或带 `role=button` + `aria-expanded` + 可见 focus ring（`--primary` 2px）；正文流式区 `aria-live="polite"`（思考区 `aria-live="off"` 防刷屏）；中断文案带图标+颜色双通道（不单靠颜色）。

### 2. 委派状态文案（core）

复用现有状态标签 status-tag 胶囊的形态，但**行内文案区分**：
- ok=normal → "领域专家分析完成"
- reason∈{idle,total,turn,failed} → "领域专家分析中断 · <原因>"
事件由 delegate end（带 ok/reason）驱动；旧的无条件"完成"被取代。

### 3. 任务/进度看板 task-board（task-board change）

**入口 chip**：主区顶栏右侧（深度思考 chip 同侧带）加紧凑 chip：SVG 列表图标 + "任务" + 进行中计数胶囊（>0 时显示，primary 底白字）；点击开合右侧面板；`aria-expanded`、focus ring。
- 运行中有 execution 时计数>0，chip 圆点呼吸提示

**右侧只读面板**：
- 复用引用抽屉同插槽（fixed right，360px，max 88vw）；**与引用抽屉互斥单开**（打开其一关闭另一，避免右缘重叠）
- 结构：头部（"任务/进度" + ✕）；主体任务列表（按更新时间倒序，plan 与 execution 分组或同列加 type 徽标——采用同列列表 + type 徽标：plan 用 outline 徽标、execution 用 primary 徽标）
- 每项：状态圆点/图标 + 标题 + 状态胶囊（running primary / done text-secondary / timeout·failed error，中断原因 11px muted 附注）+ 更新时间（11px muted）+（execution）当前阶段摘要（如"正在调用领域专家分析"或技能名）；可展开显示 summary/delegate 关联（折叠 150ms）
- **数据**：运行期由 `task` SSE 事件增量更新；**进入会话/刷新时**先 `GET /api/sessions/tasks?session_id=` 拉快照初始化（空态文案"暂无进行中的任务"）；断线续接后以快照+事件校正
- **只读**：前端无任何写入口（写仅主 agent Task 工具）

**无障碍/性能**：chip 与面板 `aria-expanded`/role=dialog；键盘可关（Esc）；列表长于 8 项内部滚动不撑高面板；状态颜色双通道（圆点+文字）；150-300ms 过渡。

## 交互状态流转（委派一次）

1. 用户提问 → 主 agent 思考/检索（现状不变）
2. delegate start：过程区新建节（呼吸点 "正在分析…"）；看板 execution 条目出现（running）
3. delegate 流式：thinking/content 增量实时上屏；看板 stage 摘要更新
4. delegate end（ok）：节圆点停呼吸，文案"领域专家分析完成"；看板条目 done
5. delegate end（中断 reason）：节文案"分析中断 · <原因>"（error 圆点）；看板条目终态带原因；主 agent 收到超时文案继续兜底整合
6. 主 agent 整合正文（现有 token 流）→ 回答完成

## 验收（playwright 冒烟要点）

- 一次委托：过程节完整、正文流式、完成文案正确、看板 done；主回答与落库无过程文本
- 多次委托：分节互不串流、各自状态正确
- 中断委托：文案含原因；看板终态含原因
- 刷新/切会话：过程区不重建（仅终稿）；看板经快照恢复
- 断线：onClose/onError 自动续接后事件续接（无重复卡片）
