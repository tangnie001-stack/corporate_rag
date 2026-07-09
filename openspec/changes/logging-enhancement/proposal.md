## Why

当前日志只记录了请求参数和返回数据量，无法通过 traceid 串联完整调用链路。排查问题时需要反复添加临时日志，效率低。需要将 SQL 返回值、ChromaDB 检索结果、API 响应体一并纳入日志，实现端到端的数据流转追踪。

## What Changes

1. **移除控制台日志 sink** — 服务器上没人看控制台，排查全凭日志文件
2. **新增 `logs/error_{date}.log` 文件 sink** — 只记录 ERROR 级别日志，按天轮转
3. **`response_envelope_middleware` 更名为 `response_processor_middleware`** — 职责扩展为返回值处理（包装 + 日志）
4. **API 响应体日志** — 非 GET 请求记录响应体内容（保护性上限 10MB）
5. **MySQL SQL 返回值日志** — 读操作记录返回数据内容
6. **ChromaDB 检索结果日志** — `similarity_search`、`similarity_search_all`、`get_chunks_by_doc_id`、`get_chunks_paginated` 记录返回数据

## Capabilities

### New Capabilities
- `data-trace-logging`: 新增数据链路追踪日志能力，通过 traceid 串联 请求 → SQL/ChromaDB 返回 → API 响应 的完整数据流

### Modified Capabilities

（无现有 spec 被修改）

## Impact

- **`src/core/logging.py`** — 移除控制台 sink，error.log 改为按天轮转 + 文件名含日期
- **`src/middleware/response_envelope.py`** → 重命名为 `response_processor.py`，函数名改为 `response_processor_middleware`，添加响应体日志
- **`src/infra/db/mysql_db.py`** — 所有读操作 SQL 方法添加返回值日志
- **`src/infra/db/vector_store.py`** — 4 个查询方法添加返回值日志
- **`src/main.py`** — 更新中间件引用名
