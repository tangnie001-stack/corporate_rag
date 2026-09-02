# retrieval-quality-signals Design

## Context

检索质量判定现状（前面 explored）：
- 离线 RAGAS（eval_ragas + eval_report 表）跑通用测试集算 4 指标——测不到用户真实问题
- 在线 agent 循环用 prompt 软引导做"不足→换词→转 web"，无结构化观测
- `_dedup_by_doc_id` 一刀切每文档留 1 条（retrieval.py:27-50），单文档占满 top-K 场景多样性受损（bug3 记录）

参考论证（claude-code / deepseek-harness / financial_rag 三项目对比）：
- claude-code 验证编码产物靠**真实执行**（build/test/lint），非 LLM judge——产物可执行用硬证据
- 本项目 RAG 产物是文本不可执行，故 faithfulness 需 LLM judge；但**检索质量判定有硬证据**（agent 拿到检索结果后能否回答），不该加前置相关性 LLM grader
- financial_rag 的 RetrievalGrader（CRAG）是纯 RAG demo 自循环，未经过多轮 agent + prompt 引导考验；本项目检索不足判定已被 agent + prompt 承担

## Goals / Non-Goals

**Goals:**
- 在线检索质量"差"可被结构化观测（行为信号，零额外 LLM）
- 行为信号可关联 trace_id 回放检索全过程
- dedup 策略支持每文档 N 条，用 RAGAS 对真实差 query 做 A/B 定最优 N

**Non-Goals:**
- 不引入 LLM RetrievalGrader（离线/在线都不加相关性打分 LLM）
- 不做生产级持续监控（定时批量盯质量属运维向，排期靠后）
- 不改检索链路的在线行为（只加观测，不加判定干预）
- 不建独立诊断表（信号先进日志，批量统计需求出现再升表）

## Decisions

### D1: 行为信号 = 在线检索质量判据（态 B 专属）

**方案**：agent 的行为轨迹就是检索质量的"真实证据"。agent 是检索结果的最终消费者，它"不满意"的行为（换词/转 web/拒答/judge 无支撑）就是缺陷投票，不需要额外 LLM 打分。

**两态限定（关键）**：
```
态 A · 未选 KB（纯对话）:  search_web 是主路径、retrieve_kb 空是设计行为
   → to_web / empty_result / reretrieve 在态 A 不激活（避免把正常联网误标缺陷）
态 B · 选 KB（RAG）:      检索不足转 web / 空结果 / 换词 才是真缺陷信号
   → 信号埋点在 kb_id 非空时才 emit
cited 对照基线两态都产（态 A 引 web、态 B 引 kb/web），带 kind 区分
```

**6 信号定义**：

| signal | 判据 | 激活态 | 含义 |
|---|---|---|---|
| `reretrieve` | 同 turn retrieve_kb 第 2+ 次调用 | B | 检索不足，agent 换词 |
| `to_web` | **仅检索降级路径**：retrieve_kb 空/无关后 agent 自主转 search_web | B | KB 检索失败降级 |
| `abstain_after_retrieve` | 检索过但 answer 命中拒答语 | B | 拿文档却说没找到 |
| `unsupported` | judge `_unsupported` 非空 | B | 答案没被检索证据撑住 |
| `cited` | format_node 有 citations | A+B | 正常（对照基线） |
| `empty_result` | retrieve_kb 返回 0 | B | 检索空（态 A 空是设计，不标） |

**to_web 排除 verify 指派联网（X 决策，2026-09-03，方案 A：ctx 标记）**：search_web 有两条触发路径——① agent 自主检索降级（retrieve_kb 空/无关 → 换词 → 转 web，prompt 规则驱动）；② verify 完整性补数据（缺失年份 → 用户确认 → 注入指引 SystemMessage 驱动）。② 属**知识库数据覆盖不足**，非检索质量问题，不得标为 to_web 缺陷信号。

**实现（方案 A：ctx 标记，2026-09-03）**：search_web 是独立 `@tool` 无 InjectedState，工具内访问不到 `state.messages`，不能靠检测 messages 里的 `VERIFY_GUIDANCE_MARKER` 判断。改为：
- `RequestContext` 加 `web_guided: bool = False`（verify 注入联网指引驱动 agent 调 search_web 的标记）
- verify_node 注入指引 SystemMessage 时同步置 `ctx.web_guided = True`（② 的唯一发生点，verify_node.py:302-312）
- search_web 工具读 ctx：`web_guided` 为 True 时本次调用判②，不产生 to_web 缺陷信号（但联网仍正常执行）
- 说明：search_web 通过 `current_request_ctx.get()` 访问 ctx（web_tools.py 已在读 ctx.web_count），加读 `web_guided` 零新增依赖
- kb_data_gap 信号不建（无消费方，YAGNI）
- **交互依赖**：C1-2 决策化后 verify 会读 search_web 的 queries 覆盖度，与 to_web 埋点同处 verify/search_web 交界 → Change 2 依赖 Change 1（agent-loop-hardening）先行落地

**埋点 helper**：依赖日志 change（logging-convention-migration）的 `retrieval_signal:` helper，query 截断 40、k=v 同构、trace_id 由 logging patcher 注入。

### D2: 定位流程 = 在线标记 → 离线确认（轻量下钻，无新表）

```
阶段一（在线，实时）：agent 行为信号 → 标记"疑似检索差"的 query
  retrieval_signal: signal=to_web query="..." iteration=N kb_id=... trace_id=...
阶段二（离线，按需）：把标记的 query 喂 RAGAS 逐条跑
  确认：这条真差（context_precision 低）还是 agent 没用好（分数够）
  下钻 detail_json → 看 dense/bm25/dedup/rerank 哪环问题
```

**不建新表**：信号先进 /data/logs；`eval_report.detail_json` 已能存单条明细，缺的是"每条 query 的检索明细写入"，补进 RAGAS 输出即可。批量统计需求出现后再升 B（消息表加列）/ C（独立表）。

### D3: dedup 多样性实验（3b，独立于信号）

`_dedup_by_doc_id` 从固定"每文档 1 条"改为可配置（settings 加 `RETRIEVAL_MAX_PER_DOC`，默认 1 保持现状）：
- A 组（现状）：每文档 1 条
- B 组：每文档 N 条（N=2/3 试）

用**现有 RAGAS 指标**对"真实差 query 小测试集"（来自 D2 阶段一标记）A/B 对照 context_recall / context_precision / faithfulness，定最优 N。**不依赖新增 grader**（修正此前"3b 依赖 3a 评估基线"的错误判断——RAGAS 现有指标即可对照）。

## 两条路线（态 A / 态 B）信号方案对照

| 环节 | 态 A（未选 KB，纯对话） | 态 B（选 KB，RAG） |
|---|---|---|
| 检索 | retrieve_kb 空返回（设计） | 真检索 → 若不足 agent 换词/web |
| 产信号 | **不产检索质量信号** | 产 reretrieve/to_web/empty_result/unsupported |
| search_web | 主路径（正常） | 降级路径（缺陷信号 to_web） |
| judge | 不跑（无 unsupported 信号） | 跑 faithfulness（unsupported 信号） |
| abstain | 可能拒答（常识不足） | 检索后拒答 = 缺陷信号 |
| 对照基线 | cited（kind=web） | cited（kind=kb/web 区分） |
| 诊断流向 | 无（纯对话质量问题走日志人工） | 标记 → 离线 RAGAS 确认 → 下钻 |

## Risks / Trade-offs

- [信号只在态 B 激活，态 A 检索质量问题不可观测] → 态 A 无 RAG 检索，不存在"检索质量"问题；纯对话质量问题属模型/联网，非本 change 范围
- [行为信号依赖日志 change 的 helper] → 两 change 有依赖：logging-convention-migration 先行或同批；信号格式在日志 spec 已定义
- [dedup N 增大可能带回重复文档噪音] → 用 RAGAS A/B 数据说话，不拍脑袋定 N；默认 N=1 保持现状零回归
- [detail_json 下钻信息不足] → 补"每条 query 检索明细写入 eval 输出"（dense/bm25/dedup/rerank 各环节），小改不动 schema
