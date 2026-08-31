# 页面规格：LLM 回答 Markdown 渲染（chat-markdown）

> 归属 change：`chat-markdown-rendering`。本文定义助手消息 Markdown 渲染的视觉规格与交互行为，实现与 mockup 以本文为准。
> 参考：deepseek-harness `MarkdownText.module.css`（作用域类 + 间距节奏 + 链接/代码/表格细节）。

## 设计目标

把 LLM 输出的结构化 Markdown（标题/加粗/列表/代码块/引用/表格）在助手气泡内渲染为可读的富文本，替换现在的纯文本平铺；对齐既有设计系统（Minimalism & Swiss，见 `MASTER.md`）；未信任内容安全渲染（XSS 过滤）。

## 作用域与隔离

- 所有 Markdown 元素样式限定在 `.bubble-content.md` 类下，**不得污染**既有聊天布局（气泡/输入区/澄清表单/侧边栏/引用卡片）
- 助手消息 → `.md` 渲染；用户消息/系统消息/澄清表单 → 保持 `escapeHtml` 纯文本（不进入 Markdown 路径）
- 历史会话回放的助手消息 → 同一 `.md` 渲染

## 视觉规格

### 全局
| 项 | 规格 |
|---|---|
| 容器 | `.bubble-content.md`，`overflow-wrap: anywhere`，继承气泡底色 `--surface`/`--bubble-ai` |
| 字体 | 正文 15px，行高 1.6（对齐 Source Sans 3 / 既有 body） |
| 文字色 | 正文 `--text-primary`(#1E293B)，次级 `--text-secondary`(#64748B) |
| 间距节奏 | 块级元素 12px 上下边距（参考 dsh 16px，按气泡内空间收窄） |

### 标题
| 级别 | 规格 |
|---|---|
| h1/h2 | 16px / 700（等同正文放大一档），上边距 20px、下边距 8px |
| h3 | 15px / 700，上边距 16px、下边距 6px（LLM 常用最高到 h3，重点样式 h3） |
| h4+ | 14px / 600 |
| 与相邻列表 | 标题下紧邻列表时收紧到 6px（防双倍间距） |

### 文本
| 元素 | 规格 |
|---|---|
| 加粗 `**` | 600（不用 700，避免过重） |
| 行内代码 `` ` `` | 13px / 等宽（Source Code Pro 或系统等宽），背景 `--primary-light`(#DBEAFE) 25% 透明度，圆角 4px，padding 1px 5px |
| 链接 | `--primary`(#3B82F6)，无下划线，hover 下划线；`target=_blank rel=noopener noreferrer` |
| 分隔线 hr | 1px，`--border`(#E2E8F0)，上下 16px |

### 列表
| 元素 | 规格 |
|---|---|
| ul/ol | 左 padding 18px，上下 8px |
| li | 间距 6px，`::marker` 用 `--text-secondary` |
| 嵌套列表 | 子列表缩进同 18px，marker 位置 inside |

### 代码块
| 项 | 规格 |
|---|---|
| pre/代码块 | 等宽 13px，背景 `#F1F5F9`，圆角 8px，内边距 12px，`overflow-x: auto`（横向滚动不撑破气泡） |
| 行号/语言标签 | 不做（后置，本 change 不含代码高亮） |

### 引用块
| 项 | 规格 |
|---|---|
| blockquote | 左边框 3px `--primary`(#3B82F6)，左 padding 12px，文字色 `--text-secondary`，上边距 8px |

### 表格
| 项 | 规格 |
|---|---|
| 包裹容器 | `overflow-x: auto`（表格超宽时横向滚动，不撑破气泡） |
| 表头 | 600，背景 `#F1F5F9`，下边框 1px `--border` |
| 单元格 | 上下 6px 左右 10px，`border-bottom: 1px solid --border` |

### 任务列表（GFM checkbox）
`[ ]`/`[x]` 渲染为原生 checkbox（只读），`accent-color: --primary`。

## 流式渲染行为

- 流式中：rAF/~60ms 节流整段重渲染（marked 对未闭合语法容错，预览前做闭合补全），格式渐进可见
- 流式结束：全量渲染一次（表格/引用编号收尾）
- 闪烁规避：渲染结果复用同一 DOM 节点，`innerHTML` 替换；如出现重排抖动，回退"结束才渲染"

## 安全约束

- 渲染管线：`marked.parse(text, {gfm:true, breaks:true})` → `DOMPurify.sanitize(html)` → `innerHTML`
- **纵深防御（验证结论）**：DOMPurify 3.1.6 主路径用 DOMParser 解析不会触发 `img onerror`，但 legacy `createHTMLDocument`+`innerHTML` 回退路径存在解析期执行风险，marked 层转义保留作纵深防御。先用 marked `html` renderer 把原生 HTML 转义/中性化（`token.text || token.raw`），再交给 DOMPurify 兜底
- 过滤 `<script>`、事件属性、`javascript:` 链接等 XSS 向量（DOMPurify 默认白名单）
- 用户消息/系统消息不进 Markdown 路径（保持 escapeHtml）

## 与既有组件的协调

- `[n]` 引用编号文本：原样渲染（`[1]` 不解析为 Markdown 链接）；citation 事件展示不变
- 思考折叠行（reasoning Think）：在 `.md` 之外，不受影响
- 引用卡片/反馈按钮：在 `.md` 容器之外，不受影响

## 验收要点

1. 长文 Markdown 答案（含 h3/加粗/列表/行内代码/代码块/表格）渲染正确
2. XSS 注入（`<img onerror>` / `<script>` / `javascript:`）被过滤
3. 流式时格式渐进、结束时完整、无闪烁卡顿
4. 既有聊天布局无回归（playwright 对比）
