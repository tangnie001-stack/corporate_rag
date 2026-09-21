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
| `[delegate]` | fork 子代理委派（skills/delegate_task + executor；事件：delegate start / delegate model turn / delegate end） |

新语义面前缀：先登记 `src/core/log_events.py` 的 `LOG_PREFIXES` + 本表加一行后启用，不预建。

## 事件命名
英文小写、空格分隔 ≤2~3 词（`search done` / `rerank skip`）；键 snake_case；禁类名/函数名/工具名下划线名作事件词。

## 值类型编码（helper 唯一实现）
- int/bool 裸写；时长整数毫秒
- 字符串 token 安全（`^[A-Za-z0-9_./:@-]+$`）裸写，否则双引号 + JSON 转义
- 数组/容器：紧凑 JSON 文本（无空格）。如 `prompt validated` / `prompt assembled`
  的 `section_chars` 为「段名 → 字符数」映射，走此编码（调用方传 dict，编码由 helper 负责）
- query/搜索词完整记录、不按固定长度截断
- 附加字段（含 retrieval_signal）同走此编码

## 级别语义
- debug — 诊断细节默认关；info — 正常里程碑；warning — 降级可恢复（禁"正常但少见"）；
  error — 单点失败已处理；exception — 透传带 traceback
- 事件级别由 EventSpec.level 登记，helper 路由；exception 直调不建 spec
- 噪声控制用**调用点守卫「不记」**，不用 debug 降级：`EventSpec.level` 只允许
  `info/warning/error` 且调用点不能逐次降级，故高频或无信息量的轮次直接不调 helper
  （非 debug）。当前守卫：`agent resolved` 在请求与绑定都为空时不记、`skill dispatch`
  在 `kind=plain` 时不记。

## trace_id / session_id
由 `src/core/logging.py` patcher 自动注入日志行第 3/4 段，业务不手写。

## 事件全集
以 `src/core/log_events.py` 的 `Event` 枚举 + `EVENT_SPECS` 为准，本文件不抄录（防双维护）。

## 来源与 prompt 观测事件

每轮来源与 prompt 组装的观测事件。字段明细以 `src/core/log_event_specs.py` 的 `EVENT_SPECS`
为准（本文件不抄录），此处只登记事件/前缀/级别与非代码可读的值域：

- `agent resolved`（session / info）——每轮生效智能体与解析来源；`source` 取 bound
  （沿用绑定）/ new_bound（首次绑定）/ ignored（请求与会话绑定不一致，已忽略）/
  unregistered（请求名未注册，降级为空）/ none
- `kb domain fallback`（session / warning）——知识库领域无对应 base 模板，回落保留值
  general；**不阻断**。`kb_id` 为发生回退的知识库、`domain` 为落库的非法值
- `agent domain mismatch`（session / info）——会话选定了预设同时又绑定了非通用领域的
  知识库；三选一替换语义下领域方法此时不参与组装，属**明确接受的代价**，非缺陷。
  `agent` 为生效预设名、`domain` 为知识库领域
- `prompt assembled`（llm / info）——system prompt 组成；`persona_source` 取 preset
  （会话预设人设）/ domain（知识库领域 base）/ general（**仅当领域未知、回落到内置通用
  base** 时出现 —— `kb_domain` 为 `general` 且有对应模板时走的是 domain 分支）；`kb_domain`
  为本次生效的领域标识；`tool_count` 为本轮注册的工具数（**`rag_tools` 全量**，含
  `task_create` / `task_get` / `task_list` / `task_update` / `task_output` / `task_stop`
  六个任务工具，生产约 8~10，不是"RAG 工具数"）；`section_chars` 为**实际拼进 system 的
  各段**字符数（容器值，紧凑 JSON，键序 = 段组装顺序 `base → runtime_contract → sources
  → tools → output`，空段不出现）；口径**不含**日期行与态 A 第二条未绑定消息，故占比
  估算系统性偏低，属预期而非缺陷）
- `prompt section share high`（llm / warning）——system 段按字符数估算占 context window
  的比例超阈值；**仅告警不阻断**。`share` 为估算占比、`est_tokens` 为估算 token 数、
  `threshold` 为当前阈值。换算系数是跨模型借用值、阈值是推断值，均非契约（见
  `src/config/settings.py` 的 Prompt 组装观测段）
- `prompt messages`（agent / info）——首轮组装的消息三段条数（system / 注入 / 历史）
- `skill injected`（session / info）——技能正文成功注入；`mode` 取 inline（命令触发）/
  preload（预设预绑定首轮预加载），`source` 取 command / preset
- `skill dispatch`（session / info）——命令形态分派；`kind` 取 plain（普通文本，不记）/
  known（命中技能）/ unknown（未注册命令）；`context` 取 inline / fork（技能 frontmatter
  的形态）/ none（命令未命中技能，record 为空）
- `model turn` 扩 `temperature` / `temp_source`（explicit 逐轮直传档 / default 模型构造档）/
  `kb_bound`，不新增事件

**不再扩 `iteration done`**：其「消息数拆分」意图已由 `prompt messages` 在组装点承载，
不重复记录同一事实（避免后人再提）。

## 已知例外
- `retrieval_signal:` 为 P1 Change 2 既有契约保留前缀，检索域聚合需
  `[retrieval]` + `retrieval_signal:` 两条 grep 模式；待前缀体系重构时统一
- skill / 智能体预设的**加载期**问题（名称非法、废弃字段、双轴推导、死 skill）走 `warnings.warn`，
  不登记为 `Event`（一次性内容问题，非请求路径）
