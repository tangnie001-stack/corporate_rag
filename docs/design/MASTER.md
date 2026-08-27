# 企业知识库 RAG 对话 — 设计系统

## 风格
**Minimalism & Swiss Style** — 简洁、留白充足、高对比度、网格化布局。

## 色彩

| 角色 | 色值 | 用途 |
|------|------|------|
| Primary | `#3B82F6` | 主按钮、AI 气泡强调色、链接 |
| Secondary | `#60A5FA` | 辅助色、次要按钮 hover |
| Accent/CTA | `#F97316` | 发送按钮、快捷选项强调 |
| Background | `#F8FAFC` | 页面/对话区背景 |
| Surface | `#FFFFFF` | 气泡卡片底色 |
| Text Primary | `#1E293B` | 正文 |
| Text Secondary | `#64748B` | 辅助文字、时间戳 |
| Border | `#E2E8F0` | 分割线、输入框边框 |
| Error | `#EF4444` | 错误提示 |

## 字体
- **Heading**: Lexend (300–700)
- **Body**: Source Sans 3 (300–700)
- 基础字号: 16px，行高 1.5

## 圆角
- 气泡: 12px (AI) / 16px (用户)
- 快捷选项: 8px
- 输入框: 10px

## 阴影
- 气泡: `0 1px 3px rgba(0,0,0,0.08)`
- 输入框聚焦: `0 0 0 3px rgba(59,130,246,0.15)`

## 间距
- 气泡间距: 16px
- 选项按钮间距: 8px
- 内容内边距: 16px

## 组件规格
按页登记于 `docs/design/pages/`，此处为组件索引：

| 组件 | 页面文档 | 关键规格 |
|------|---------|---------|
| 状态标签 status-tag | `agentic-clarification.md` | 胶囊 + 蓝色呼吸圆点 |
| 追问卡片 composer | `agentic-clarification.md` | **输入区接管**（不进消息流）、选项卡片选中 primary-light、提交 primary |
| abstention 转人工 | `agentic-clarification.md` | amber 提示条 + accent 橙 outline 按钮 |
| 答案反馈 feedback | `agentic-clarification.md` | 28×28 SVG 按钮、选中 primary 高亮 |
| 模型信息 model_info | `agentic-clarification.md` | 11px muted、fallback amber 徽标 |
| 引用卡片 citation | `agentic-clarification.md` | 编号 badge + 来源 + snippet、hover primary 边框 |
| 深度思考开关 thinking-toggle | `agentic-clarification.md` | 输入区紧凑 chip、选中 primary、控制 `enable_thinking` |

新组件/页面设计时：全局基线更新到本文件，页面级规格写入 `pages/<name>.md`，效果预览输出 `docs/design/<name>-mockup.html`。
