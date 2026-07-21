## Why

RAG 问答链路（用户提问 → 检索 → 重排序 → 生成）当前在服务端缺少足够的 INFO 级别日志，无法通过日志追踪每个环节的数据状态和耗时。排查线上问题时只能看到"出错了"的 exception 日志，看不到"检索命中多少条、重排序过滤后剩多少条、生成花了多久"。补全日志后可以通过一条 trace_id 串联整个请求的生命周期。

本次是文档上传链路日志补全（已完成）的延续，目标一致：让 INFO 日志覆盖链路的每个节点。

## What Changes

- 在 `api/chat.py` 的 SSE 流式入口添加请求开始/完成日志 + 四段阶段耗时（检索/重排序/生成/总计）
- 在 `rag/chain.py` 的同步入口添加请求开始/完成日志 + 四段阶段耗时
- 在 `rag/retrieval.py` 的检索和重排序函数中添加搜索模式（hybrid/dense）和重排序结果明细日志
- 在 `rag/stream.py` 的生成函数中添加生成完成日志（耗时、token 用量、字符数）
- 在 `api/chat.py` 的会话持久化成功后添加确认日志
- 所有改动力求最小，仅加 logger.info 和 import time，不改业务逻辑

## Capabilities

### New Capabilities

（无。本次不引入新的用户可见能力，纯内部可观测性改进。）

### Modified Capabilities

（无。未修改任何已有 spec 定义的 behavior 级别的需求。）

## Impact

- 改动文件：`src/api/chat.py`、`src/rag/chain.py`、`src/rag/retrieval.py`、`src/rag/stream.py`
- 无新增依赖，无配置变更，无 API 变更
- 日志量增加：每个 RAG 请求约增加 3~5 行 INFO 日志
- 所有改动均为加法，不改业务逻辑，风险极低
