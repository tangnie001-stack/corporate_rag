# 追问对话 — 页面设计

## 组件清单

### ClarificationBubble (追问气泡)
- **位置**：聊天流中 AI 回答的位置，替代普通回答气泡
- **外观**：白色圆角卡片 (#FFFFFF)，左侧 AI 头像，与普通 AI 气泡外观一致
- **触发**：收到 SSE `event: clarification` 时渲染
- **状态变化**：隐藏加载中指示器 → 渲染气泡 → 聚焦补充输入框

### SuggestionChips (快捷选项)
- **位置**：追问气泡内，紧跟在追问文本下方
- **样式**：圆角 pill 按钮，Primary 边框 `#3B82F6`，hover 填充浅蓝背景
- **交互**：点击后自动发送对应 query + 复用 session_id → 隐藏追问气泡

### ClarificationInput (追问输入框)
- **位置**：追问气泡底部，快捷选项下方
- **样式**：圆角输入框 + Accent 发送按钮 (#F97316)
- **交互**：回车/点击发送 → 同 session_id 发新请求

### ChatInput 调整
- 追问期间不禁用输入框
- 追问期间输入框 placeholder 改为 "输入补充信息…"
- 发送时自动带 session_id

## 交互状态机

```
正常会话 → 发送 → 等待回答(streaming)
                   ↓ SSE: clarification
              等待追问(呈现气泡)
                   ↓ 用户点击选项/输入
              重新发送(同session_id)
                   ↓
              等待回答(streaming)
                   ↓ SSE: done
              正常会话
```

## 追问气泡布局 (从上到下)

1. AI 头像 (32x32, Primary 色圆) + "系统" 标签
2. 追问文本（`question` 字段内容）
3. 快捷选项列表（横向 wrap，最多 4 个）
4. 补充输入框 + 发送按钮
