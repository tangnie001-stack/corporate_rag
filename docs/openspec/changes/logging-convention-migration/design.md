# logging-convention-migration Design

## Context

全系统 239 处 logger 调用无统一 message 格式规范，实测三类混乱：
1. **中英混用**：cli/知识图谱/入口约 19 处中文叙述 + 绝大多数英文技术日志并存
2. **事件"身份"三种写法**：类名前缀（`KBRouter:` `format_node:`）/ 裸动词（`verify skipped` `agent iteration=`）/ k=v 流（`tool=` `judge:`）
3. **同类事件措辞分散**：检索动作 ≥5 种开头（RAG search / search failed / rerank failed / tool=retrieve_kb / judge:）；级别语义与 rules.md 脱节（warning 81/239、debug 6/239）

现有地基（保留不动）：
- `src/core/logging.py` `_LOG_FORMAT`：`时间 | level | trace_id | name:function:line - message`，trace_id 由 patcher 自动注入第三段
- rules.md「日志约定」只有级别选择（warning vs exception），无 message 格式规范
- 好苗头：`tool=` / `judge:` k=v 流已局部存在，需文档化收口

## Goals / Non-Goals

**Goals:**
- 立统一规范（分层前缀 + 英文 k=v + 五级语义）进 rules.md + CLAUDE.md 简则
- 定义检索行为信号日志（`retrieval_signal:`，P1 Change 2 诊断地基）
- 提供统一日志 helper，query 截断/格式收口
- 存量迁移机制（分批清单），**文档先行、存量后迁**

**Non-Goals:**
- 本次不批量改存量 239 处（分批任务 3.x 后续逐模块执行）
- 不改 `_LOG_FORMAT` / trace_id 注入机制 / 日志文件轮转
- 不动用户可见文案（SSEInteractionTexts 里的中文保持）
- 不引入日志采集/告警系统（filebeat/监控属运维向）

## Decisions

### D1: 事件前缀 = 分层前缀（方案 A）

```
[retrieval] ...  检索层（rag_tools / web_tools / retrieval.py）
[verify] ...     验证节点（verify 包）
[agent] ...      agent 主循环（agent_node / workflow）
[session] ...    会话管理（chat manager / persistence）
[db] ...         DB 层（repo）
[llm] ...        LLM 调用层
```

**核心原则：同一类事件全系统只有一个统一前缀 + 措辞**，可用一条 grep 聚合。弃类名前缀（`KBRouter:`）——它把"类"当事件身份，同类事件跨类就散了。

### D2: message = 英文小写 k=v

```
✅ logger.info("[retrieval] search start query={} kb_id={}", query, kb_id)
❌ logger.info("RAG search starting hybrid: kb_id={}", kb_id)
❌ logger.info("加载测试集: {} 条 QA 对", n)
```

中文仅限用户可见文案（`SSEInteractionTexts`）。事件消息是给 grep + 开发者看的，机器要能解析。

### D3: 五级级别语义（扩 rules.md 现有「日志约定」）

| 级别 | 语义 | 示例 |
|---|---|---|
| `debug` | 诊断细节，默认关闭 | 检索中间态、token 流 |
| `info` | 正常流程里程碑 | 请求进出、agent 迭代、检索/联网完成 |
| `warning` | 降级/可恢复异常 | fallback 生效、重试、超时 |
| `error` | 单点失败已处理 | LLM 失败降级 |
| `exception` | 透传型异常（带 traceback + raise） | 透传型（rules.md 已有定义） |

**关键纠正**：warning 泛滥（81 处）源于拿它记"正常但少见"——warning 只留给"需要人注意的可恢复降级"。

### D4: 检索行为信号日志（纳入统一体系，P1 Change 2 依赖）

```
前缀: retrieval_signal:
类型: reretrieve / to_web / abstain_after_retrieve / unsupported / cited / empty_result
格式: retrieval_signal: signal={} query="{}" iteration={} kb_id={} result_count={} ...
trace_id: logging patcher 自动注入第三段，不写入 message
```

helper 收口 `src/core/log_signal.py`（或并入 logging.py），query 截断 40、k=v 拼装统一。**query 加引号**便于按字段解析（与 `tool=` 裸值现状的权衡：信号行是新格式，从第一天就结构化）。

### D5: 存量迁移分批机制

- 盘点 239 处 → 生成迁移清单（按模块分组标注目标前缀）
- 分批执行（tasks 3.1~3.5），每批独立 commit + `pytest` 回归
- 每批抽查：同层事件一条 grep 可全捞；无中英混行（除用户可见文案）

## 两条路线（态 A / 态 B）对照

日志规范本身**两态通用**（都是这套前缀/k=v/级别体系），两态差异只在**具体事件内容**：

| 事件 | 态 A（未选 KB，纯对话） | 态 B（选 KB，RAG） |
|---|---|---|
| `[retrieval]` | 少（retrieve_kb 空返回，可能无） | 多（真检索各环节） |
| `retrieval_signal:` | **不产**缺陷信号（见 Change 2 D1 态限定） | 产全部 6 种信号 |
| `[verify]` | 态 A 管道日志（web_citation_guard） | 态 B 管道日志（completeness/护栏/judge） |
| `[agent]` | 主循环日志（无 retrieve 轮） | 主循环日志（含 retrieve 轮） |

分层前缀体系让两态在同一前缀下自然共存——grep `[verify]` 能看到两态各自管道在跑，再靠 k=v（kb_id 有/无）区分。

## Risks / Trade-offs

- [文档先行 + 存量后迁期间新旧格式并存] → 分批迁移 + 每批抽查收敛；新代码写日志前先查规范
- [query 加引号与既有 `tool=` 裸值不一致] → 信号行是新格式第一天就结构化；存量迁移时顺带统一裸值字段
- [中文日志迁移遗漏（cli 工具非核心链路）] → 盘点清单覆盖全部 src；cli 属低优先但列入 3.5
- [helper 与 logging patcher 职责边界] → helper 只做 message 拼装，不重复注入 trace_id（patcher 已做）
