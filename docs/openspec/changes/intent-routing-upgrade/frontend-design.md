# 追问对话 — 前端设计文档

## 交互流程

### 完整生命周期

```
用户               前端               后端 API               SSE 流
 │                  │                   │                     │
 ├─ 输入问题 ──────►│                   │                     │
 │                  ├─ POST /chat/stream ──►                  │
 │                  │   {query, session_id, kb_id}            │
 │                  │                   │                     │
 │                  │                   ├─ classify_node ─────►
 │                  │                   │  发现缺年份实体     │
 │                  │                   │                     │
 │                  │◄───── SSE 事件流 ──────────────────────┤
 │                  │  event: status    {stage:"classify"}    │
 │                  │  event: clarification                   │
 │                  │    {type:"entity_completion",           │
 │                  │     question:"请问您想查询哪一年的数据？",
 │                  │     missing_entities:[{"type":"year"}],
 │                  │     suggestions:["2023年","2024年","其他"]}
 │                  │  event: done       {}                   │
 │                  │                   │                     │
 │   显示追问 UI ◄──┤                   │                     │
 │                  │                   │                     │
 ├─ 点击"2024年" ──►│                   │                     │
 │                  ├─ POST /chat/stream ──►                  │
 │                  │   {query:"2024年",                      │
 │                  │    session_id:"xxx",   ← 同一 session   │
 │                  │    kb_id:"xxx"}                         │
 │                  │                   ├─ classify_node ─────►
 │                  │                   │  从 history 推断    │
 │                  │                   │  route=medium       │
 │                  │                   │  entities完整       │
 │                  │                   │  missing_entities=[]│
 │                  │                   │                     │
 │                  │◄───── SSE 事件流 ──────────────────────┤
 │                  │  event: status/retrieving              │
 │                  │  event: status/generating              │
 │                  │  event: token   {token:"2024年"}        │
 │                  │  event: citation                       │
 │                  │  event: done                           │
 │                  │                   │                     │
 │   显示回答 ◄────┤                    │                     │
```

### 追问流程图

```
  ┌──────────────────────┐
  │  用户提交 query      │
  │  "营收多少"          │
  └──────────┬───────────┘
             │
             ▼
  ┌──────────────────────┐
  │  classify_node       │
  │  LLM 缺实体检测      │
  │  missing=[{year}]    │
  └──────────┬───────────┘
             │ missing_entities 非空
             ▼
  ┌──────────────────────┐
  │  Graph → END         │
  │  agent_service 捕获  │
  │  → SSEClarification  │
  └──────────┬───────────┘
             │
             ▼
  ┌──────────────────────┐
  │  前端收到             │
  │  event: clarification│
  ├──────────────────────┤
  │  ① 停止显示 "输入中"│
  │  ② 显示追问气泡      │
  │  ③ 展示快捷选项按钮  │
  │  ④ 启用输入框        │
  └──────────┬───────────┘
             │
  ┌──────────┴───────────┐
  │  用户操作             │
  │  ├─ 点击快捷选项      │
  │  │  → 自动带session_id│
  │  │  → 发送新请求      │
  │  │     query="2024年" │
  │  │                    │
  │  └─ 手动输入         │
  │     → 同 session_id  │
  │     → 发送新请求      │
  └──────────┬───────────┘
             │
             ▼
  ┌──────────────────────┐
  │  后端正常处理         │
  │  SSE: token/citation │
  │  → 正常回答          │
  └──────────────────────┘
```

---

## SSE 事件契约

### 新事件：clarification

```
event: clarification
data: {
  "type": "entity_completion",        // 固定值
  "question": "请问您想查询哪一年的数据？",
  "missing_entities": [
    {
      "type": "year",                  // year/quarter/month/company/metric
      "question": "请问您想查询哪一年的数据？"
    }
  ],
  "suggestions": ["2023年", "2024年", "其他"]
}
```

### 前端状态机

```
  ┌─────────┐    POST /chat/stream     ┌──────────────┐
  │ 正常会话  │ ───────────────────────► │  等待回答     │
  │ (等待输入)│                          │ (streaming)   │
  └─────────┘◄────────────────────────└──────┬───────┘
       │          SSE: token/citation/done    │
       │                                      │ SSE: clarification
       │                                      ▼
       │                               ┌──────────────┐
       │                               │  等待追问     │
       │                               │ (等待补充信息)│
       │                               └──────┬───────┘
       │                                      │ 用户提交补充query
       │                                      │ (同session_id)
       │                                      ▼
       │                               ┌──────────────┐
       │                               │  重新等待回答  │
       │                               │ (streaming)   │
       └───────────────────────────────┘              │
                SSE: token/citation/done ◄─────────────┘
```

### 前端需处理的事件类型

| SSE event | 已有/新增 | 处理动作 |
|-----------|----------|----------|
| `status` | 已有 | 显示阶段状态文字 |
| `token` | 已有 | 追加回答文本 |
| `citation` | 已有 | 显示引用来源 |
| **`clarification`** | **新增** | **切换 UI 到追问模式** |
| `model_info` | 已有 | 显示模型信息 |
| `error` | 已有 | 显示错误提示 |
| `done` | 已有 | 结束流 |

---

## 前端 UI 组件定义

### 追问气泡（Clarification Bubble）

```
┌─────────────────────────────────────┐
│  ┌─────────────────────────────┐    │
│  │ 🤖 系统                     │    │
│  │                             │    │
│  │ 请问您想查询哪一年的数据？    │    │
│  │                             │    │
│  │  ┌──────┐  ┌──────┐  ┌───┐ │    │
│  │  │2023年│  │2024年│  │其他│ │    │
│  │  └──────┘  └──────┘  └───┘ │    │
│  │                             │    │
│  │  ┌──────────────────┐ ┌──┐ │    │
│  │  │ 其他年份请输入…  │ │发送│ │    │
│  │  └──────────────────┘ └──┘ │    │
│  └─────────────────────────────┘    │
│                                     │
│  ┌─────────────────────────────┐    │
│  │ 👤 用户                     │    │
│  │ 2024年                      │    │
│  └─────────────────────────────┘    │
│                                     │
│  ┌─────────────────────────────┐    │
│  │ 🤖 系统                     │    │
│  │ 2024年营收为 XXX 亿元…     │    │
│  │                             │    │
│  └─────────────────────────────┘    │
└─────────────────────────────────────┘
```

### 组件清单

| 组件 | 说明 | 交互 |
|------|------|------|
| **ClarificationBubble** | 追问气泡，替代回答气泡 | 收到 `event: clarification` 时渲染 |
| **SuggestionChips** | 快捷选项按钮组 | 点击 → 自动发送 query + 复用 session_id |
| **ClarificationInput** | 追问输入框（可选的自由输入） | 回车或点击发送 → 同上 |
| **ChatInput** | 原输入框（需调整） | 追问期间不禁用，但发送时自动带 session_id |

### 追问气泡行为

1. 收到 `event: clarification` 时：
   - 停止正在展示的加载/打字状态
   - 渲染 `ClarificationBubble` 显示 `question`
   - 渲染 `SuggestionChips` 显示 `suggestions`
   - 聚焦 `ClarificationInput`
   - 不显示"回答结束"或"done"相关提示

2. 用户操作后：
   - 自动隐藏追问气泡（或维持显示作为历史）
   - 发送新请求（同 session_id）
   - 重新进入 SSE 监听状态

3. API 调用示例：
```javascript
// 首次请求
POST /chat/stream
{
  "query": "营收多少",
  "session_id": "sess_abc123",
  "kb_id": "kb_001"
}

// 追问后二次请求（同 session_id）
POST /chat/stream
{
  "query": "2024年",
  "session_id": "sess_abc123",  // 同一 session
  "kb_id": "kb_001"
}
```
