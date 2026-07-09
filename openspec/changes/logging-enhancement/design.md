## Context

当前日志系统缺乏数据链路追踪能力。日志只记录了请求参数和返回数据量（如 `count=5`），没有实际数据内容。排查问题时无法通过 traceid 查看"用户问了什么 → 数据库返回了什么 → API 返回了什么"的完整链条。

### 当前状态

- TraceID 已全链路注入（通过中间件 + Loguru patcher + ContextVar）
- 日志输出：控制台（stderr）+ `logs/app_{date}.log`（INFO+）+ `logs/error.log`（ERROR+）
- MySQL SQL 日志只记录 SQL 语句和影响行数/存在性
- ChromaDB 日志只记录操作类型和结果数量
- API 响应日志散落在各 handler，无统一记录
- 控制台 sink 无人查看

### 约束

- 不能引入新的外部依赖
- 日志行不能超过 10MB（保护性上限）
- 非 GET 接口的记录响应体，GET 接口不记录
- SSE 流式接口（`/api/chat/stream`）和健康检查（`/api/health`）不记录

## Goals / Non-Goals

**Goals:**
- 通过 traceid 可查到：API 请求参数 → SQL/ChromaDB 返回值 → API 响应体
- 所有日志集中在 `logs/app_{date}.log`，单文件可 grep 全链路
- 报错日志同步写入 `logs/error_{date}.log`

**Non-Goals:**
- 日志鉴权/脱敏（本次不做）
- 日志级别动态调整（不改配置中心）
- 历史日志迁移

## Decisions

### D1: 移除控制台 sink，改 error.log 为按天轮转

- **方案**：删除 `logger.add(sys.stderr, ...)`；`error.log` 改为 `error_{date}.log`，按天轮转保留 30 天
- **原因**：服务器运行中无人查看控制台，排查全凭文件日志

### D2: response_envelope_middleware 扩展为 response_processor_middleware

- **方案**：在原中间件中追加响应体日志逻辑，函数和文件统一更名
- **原因**：返回值包装和响应体日志都是"返回值处理"的职责，归到同一处可复用已反序列化的 `data`，避免重复读 stream

### D3: MySQL 返回值日志 — 读操作记录完整数据，写操作只记录影响行数

- **方案**：SELECT 类方法在 `return` 前记录 `rows` 或 `row` 的内容，超过 10MB 截断；INSERT/UPDATE/DELETE 保持现有日志不变
- **原因**：该方案能完整覆盖排查需求（SQL 返回了哪些数据），同时避免冗余
- **特例**：`get_messages` 只记录 count，不记录 content（对话内容可能很长）；可通过 `_SKIP_FULL_LOG_METHODS` 集合统一管理，方便后续增删

### D4: ChromaDB 返回值日志 — 只记录检索结果

- **方案**：`similarity_search`、`similarity_search_all`、`get_chunks_by_doc_id`、`get_chunks_paginated` 记录返回值
- **原因**：RAG 链路的关键环节，检索到的 chunk 内容直接决定 LLM 输出质量

### D5: app.log 文件 sink 启用 enqueue=True

- **方案**：app.log 的 `logger.add` 增加 `enqueue=True` 参数
- **原因**：新增的数据日志可能很大（MB 级），同步写入会阻塞 event loop，必须异步写入

### D6: 统一的日志前缀格式

```python
# 数据链路追踪日志统一前缀
"[SQL] method={method_name} | rows={count} | data={data}"
"[CHROMA] method={method_name} | rows={count} | data={data}"
"[API] {method} {path} | status={status} | data={data}"
```

**原因**：通过 `grep "trace_xxx" logs/app_{date}.log | grep "\[SQL\]"` 即可过滤出指定 traceid 的 SQL 数据，格式统一便于拼链路。

### D7: 跳过全量日志的集中配置

在 `src/core/logging.py` 中集中定义两个模块级集合，SQL 和 API 两层的跳过名单放在同一处，方便统一管理：

```python
# SQL 方法层 — 跳过全量返回值记录（只记 count + 关键参数）
SQL_SKIP_FULL_LOG = {"get_messages"}

# API 路由层 — 跳过全量响应体记录（只记 count/状态）
API_SKIP_FULL_LOG = {"/api/sessions/messages"}
```

**`mysql_db.py`** 引用 `SQL_SKIP_FULL_LOG`，配合辅助函数：
```python
from src.core.logging import SQL_SKIP_FULL_LOG

def _log_sql_result(method: str, rows, **extra):
    count = len(rows) if isinstance(rows, (list, dict)) else (1 if rows is not None else 0)
    if method in SQL_SKIP_FULL_LOG:
        extra_str = " | ".join(f"{k}={v}" for k, v in extra.items())
        logger.info("[SQL] method={} | rows={} | {}", method, count, extra_str)
    else:
        try:
            logger.info("[SQL] method={} | rows={} | data={}", method, count, rows)
        except Exception:
            logger.info("[SQL] method={} | rows={} | data=<serialization_error>", method, count)
```

**`response_processor.py`** 引用 `API_SKIP_FULL_LOG`，对名单中的路由跳过全量响应体日志（只记录 path 和 status_code）。

后续需要添加/删除跳过项时，只改 `src/core/logging.py` 一个文件即可。

## Risks / Trade-offs

- **[日志膨胀]** 全量记录 SQL 返回值和 API 响应体会让 `app_{date}.log` 增大 → 已设 10MB 保护性上限 + 7 天自动清理
- **[隐私风险]** SQL 返回值可能含 password hash、token 等敏感字段 → 但用户明确表示本次不处理合规问题
- **[新增依赖]** 无，全部基于 Loguru 现有能力
