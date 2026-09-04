# 日志格式规范（唯一归属文档）

## 行模板
`[prefix] 事件名 key=value ...`；`retrieval_signal:` 行保留（见「已知例外」）。
示例：`[retrieval] search done kb_id=k1 query_len=12 result_count=8`

## 前缀主表（开放登记制）
| 前缀 | 归属 |
|---|---|
| `[retrieval]` | 检索层（rag_tools / web_tools / retrieval.py） |
| `[verify]` | 验证节点（verify 包） |
| `[agent]` | agent 主循环（agent_node / workflow） |
| `[session]` | 会话管理（chat manager / persistence） |
| `[db]` | DB 层（repo / engine） |
| `[llm]` | LLM 调用层 |
| `[cli]` | 离线工具（cli/） |
| `[app]` | 应用边界（main.py 生命周期 + 全局异常兜底） |

新语义面前缀：先登记 `src/core/log_events.py` 的 `LOG_PREFIXES` + 本表加一行后启用，不预建。

## 事件命名
英文小写、空格分隔 ≤2~3 词（`search done` / `rerank skip`）；键 snake_case；禁类名/函数名/工具名下划线名作事件词。

## 值类型编码（helper 唯一实现）
- int/bool 裸写；时长整数毫秒
- 字符串 token 安全（`^[A-Za-z0-9_./:@-]+$`）裸写，否则双引号 + JSON 转义
- 数组/容器：紧凑 JSON 文本（无空格）
- query/搜索词完整记录、不按固定长度截断
- 附加字段（含 retrieval_signal）同走此编码

## 级别语义
- debug — 诊断细节默认关；info — 正常里程碑；warning — 降级可恢复（禁"正常但少见"）；
  error — 单点失败已处理；exception — 透传带 traceback
- 事件级别由 EventSpec.level 登记，helper 路由；exception 直调不建 spec

## trace_id / session_id
由 `src/core/logging.py` patcher 自动注入日志行第 3/4 段，业务不手写。

## 事件全集
以 `src/core/log_events.py` 的 `Event` 枚举 + `EVENT_SPECS` 为准，本文件不抄录（防双维护）。

## 已知例外
- `retrieval_signal:` 为 P1 Change 2 既有契约保留前缀，检索域聚合需
  `[retrieval]` + `retrieval_signal:` 两条 grep 模式；待前缀体系重构时统一
