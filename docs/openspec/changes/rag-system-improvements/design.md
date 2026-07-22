## Context

RAG 系统的 LLM 调用有三个入口：生产问答（`rag/chain.py`）、RAGAS 评估（`eval_ragas.py`）、测试集生成（`eval_ragas_generate.py`）。其中评估入口直接 new `ChatOpenAI(...)`，绕过了 `get_llm()` 工厂。三个入口分散导致无法统一加日志、切换后端。

CLI 模式（评估、生成、调试脚本）的日志缺少 `trace_id`，排查问题时无法像 API 模式那样用 `trace_xxx` grep 串联全链路。

财务报表中的大表格在跨页合并后整块保留（最多 4096 字符），导致 RAGAS 评估的 NLI prompt 过长（8741 字符），faithfulness 指标超时。表格间的短文本（如"注：以上数据来自审计报告"）独立成小块，检索质量差。

## Goals / Non-Goals

**Goals:**
- 所有 LLM 创建走 `get_llm()` 一个入口
- 所有 CLI 入口的日志行带上 trace_id，可与 API 模式互通
- 大表格 >2000 字符按行边界切分，复制表头到子块
- 孤立短文本（<200 字符）自动粘到相邻表格

**Non-Goals:**
- 不引入新的 LLM 监控平台（Langfuse 等待后续）
- 不改动 RAGAS 库内部代码
- 不做图片/图表的多模态处理

## Decisions

### 1. get_llm() 收口

**方案：** `get_llm()` 加 `**kwargs`，保持返回类型 `ChatOpenAI` 不变。

```
def get_llm(model=LLM_MODEL, temperature=LLM_TEMPERATURE, **kwargs) -> ChatOpenAI:
    return ChatOpenAI(..., **kwargs)
```

`eval_ragas.py` 中替换：
- `ChatOpenAI(model=..., temperature=0, ...)` -> `get_llm(model=eval_model, temperature=0)`
- `LangchainLLMWrapper(llm)` -> `LangchainLLMWrapper(llm, bypass_n=True)`

### 2. CLI trace_id

**方案：** 在 `logging.py` 的 `_setup_trace_id_patcher()` 中，当 ContextVar 为空时自动生成。

```
def _setup_trace_id_patcher():
    if not _trace_var.get():
        _trace_var.set(f"trace_{uuid.uuid4()}")
    # ... 原有 patcher 逻辑
```

API 模式不受影响：middleware 在请求到达时写入 `current_trace_id.set()` 覆盖掉自动生成的。patcher 每行日志读的是当前 contextvar 的值。

所有 CLI 入口统一改一行：
- `eval_ragas.py`: `setup_logging()` -> `setup_logging(configure_trace_id=True)`
- `check_retrieval.py`: `setup_logging()` -> `setup_logging(configure_trace_id=True)`

### 3. 表格三段式流水线

在 `table_preserving.py` 中按顺序执行三个阶段：

**阶段 1：跨页合并（改现有逻辑）**
- 去掉 `MAX_TABLE_CHARS` 上限检查（line 95-96, 111-112）
- 同结构表格中间只要 <100 字符就合并，不管合起来多大

**阶段 2：残差短文本合并（新增 `_merge_orphan_texts`）**
- 扫描 text segment，<200 字符且与 TABLE 相邻 -> 粘到 TABLE 上
- 优先向后合并（粘到后一个表格），其次向前合并（粘到前一个表格）
- 迭代扫描直到没有新的合并

**阶段 3：大表格行级切分（新增 `_split_large_tables`）**
- 检测 >2000 字符的 table segment
- 提取表头行（第一行 `|...|`）和分隔行（`|---|`）
- 数据行贪心分组 ~2000 字符/组
- 每组前复制表头+分隔行

### 配置参数

| 参数 | 默认值 | 类型 | 说明 |
|---|---|---|---|
| `TABLE_ROW_CHUNK_CHARS` | 2000 | int | 大表格行级切分阈值（字符数）|
| `ORPHAN_THRESHOLD_CHARS` | 200 | int | 残差短文本合并阈值（字符数）|

## Risks / Trade-offs

- **get_llm 收口**：加入 `**kwargs` 后，如果未来底层 SDK 变更（如从 `ChatOpenAI` 换成其他客户端），需确保 kwargs 兼容。风险低，因为 kwargs 只透传，不解析。
- **trace_id 自动生成**：API 模式下 middleware 会在请求到达时覆盖自动生成的 trace_id，不影响现有链路追踪。但 CLI 模式生成的 trace_id 不会出现在 Langfuse 中（因为 Langfuse 未开启），只用于日志 grep。
- **跨页合并没有上限**：一个极端场景下 200 页同一张表会全部合并成一个 segment（600K+ 字符），然后被 `_split_large_tables` 切碎。不会出错，但会生成大量子块。如果实际数据出现这种情况，可以加一个软上限（如 10 页）作为兜底。
- **短文本合并**：合并后短文本语义上属于表格的一部分，检索时不会影响精确度。但如果短文本包含独立查询意图（如表格后的独立段落"更多信息请参阅 XX 政策"），粘到表格上可能导致误匹配。目前阈值 200 字符+仅相邻表格的规则足够保守。
