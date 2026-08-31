# chat-markdown-rendering Specification

## Purpose
TBD - created by archiving change chat-markdown-rendering. Update Purpose after archive.

## Requirements

### Requirement: 助手消息 Markdown 渲染

助手消息气泡 SHALL 将 LLM 输出的 Markdown 渲染为富文本（标题/加粗/列表/代码块/引用块/表格/行内代码），不得再以原始 Markdown 语法平铺显示。渲染过程 SHALL 经 sanitize 后再插入 DOM。

#### Scenario: 流式渲染
- **WHEN** 助手消息流式输出 token
- **THEN** 界面 SHALL 渐进显示渲染后的格式（节流重渲染），结束 SHALL 全量渲染完整格式

#### Scenario: 富文本元素
- **WHEN** 消息含 `###` 标题、`**` 加粗、`*` 列表、代码块、表格
- **THEN** 界面 SHALL 渲染为对应的标题/加粗/列表/代码块/表格样式

#### Scenario: 未闭合语法容错
- **WHEN** 流式中途消息含未闭合的 Markdown 标记（如未闭合的 `**` 或代码块）
- **THEN** 渲染 SHALL 不崩溃、不显示异常字符，结束后恢复正常

### Requirement: 渲染安全

助手消息内容（来自 LLM 与 web 搜索结果）SHALL 视为未信任内容，渲染前 SHALL 经白名单 sanitize（过滤 `<script>`、事件属性、`javascript:` 链接等 XSS 向量）。

#### Scenario: XSS 注入被过滤
- **WHEN** 消息含 `<img onerror=...>` 或 `<script>` 或 `javascript:` 链接
- **THEN** 渲染结果 SHALL 不执行脚本、不加载恶意资源，仅显示安全文本

#### Scenario: 用户消息不渲染 markdown
- **WHEN** 用户消息含 Markdown 或 HTML
- **THEN** 用户消息 SHALL 保持纯文本（escapeHtml），不进入 markdown 渲染路径

### Requirement: 样式作用域

Markdown 元素样式 SHALL 限定在助手消息作用域内（如 `.md` 类），不得污染现有聊天布局；样式规格 SHALL 对齐全局设计系统。

#### Scenario: 样式隔离
- **WHEN** 应用 markdown 元素 CSS
- **THEN** 既有聊天布局（气泡/输入区/澄清表单）SHALL 不受影响

#### Scenario: 历史回放渲染
- **WHEN** 加载会话历史中的助手消息
- **THEN** 历史助手消息 SHALL 与实时消息走同一 Markdown 渲染路径

### Requirement: 引用兼容

Markdown 渲染 SHALL 不破坏现有 `[n]` 引用编号文本与 citation 事件展示。

#### Scenario: 引用编号保留
- **WHEN** 消息含 `[1]` 引用编号
- **THEN** 编号文本 SHALL 原样渲染，引用事件展示不受影响
