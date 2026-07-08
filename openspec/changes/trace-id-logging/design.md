## Context

项目已有完整的 trace_id 基础设施：中间件从 HTTP 头提取 trace_id 并设置 ContextVar，loguru 配置了 patcher 将 trace_id 注入每条日志的 `extra` 字典。但缺少最后一步——日志 sink 的 format 未渲染 `{extra[trace_id]}`，导致 trace_id 在日志文本中不可见。同时数据库、文件存储、向量数据库等关键操作缺少结构化日志，无法通过 trace_id 串联请求链路。

## Goals / Non-Goals

**Goals:**
- 所有日志文本中出现 trace_id（通过 loguru format）
- 数据库 SQL 操作日志可关联 trace_id
- MinIO / ChromaDB 等外部服务调用日志可关联 trace_id
- RAG 链关键阶段日志可关联 trace_id
- 后台异步任务日志可关联 trace_id
- 3 个使用 stdlib logging 的文件改为 loguru

**Non-Goals:**
- 接入 OpenTelemetry（留待 ARMS 独立决策）
- 修改业务逻辑
- 修改日志级别策略以外的现有日志内容
- CLI 工具的 trace_id 支持（无请求上下文）

## Decisions

### 1. 日志格式与路径
```
format: "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | {extra[trace_id]:32} | {message}"
path:   $LOG_DIR/app_{time:YYYY-MM-DD}.log  (默认 logs/，Docker 设为 /data/logs/)
rotation: 1 day, retention: 7 days, level: INFO
```
日志目录在应用启动时自动创建。

### 2. MySQL 操作日志策略
每个关键方法在 cursor.execute 之后、return 之前加一行 logger.info，包含操作名、参数值和结果特征。

### 3. MinIO 操作日志策略
asyncio.to_thread 调用后立即加日志，调用方已有 trace_id。

### 4. ChromaDB 操作日志策略
增/删/查三个关键入口加日志。同步方法中 loguru format 修复后自动带 trace_id。

### 5. RAG 链日志策略
检索条数、精排过滤比、首 token 耗时三个关键指标。

## Risks / Trade-offs
- [日志文件增长] → 按天轮转 + 7 天保留，INFO 级别
- [日志目录不存在] → os.makedirs + exist_ok=True
- [线程池无 trace_id] → 在调用方记录，线程内不需要
