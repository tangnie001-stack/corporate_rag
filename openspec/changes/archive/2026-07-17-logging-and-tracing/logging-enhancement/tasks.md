## 1. 日志配置修改

- [ ] 1.1 `src/core/logging.py`: 移除控制台 sink（删除 `logger.add(sys.stderr, ...)`）
- [ ] 1.2 `src/core/logging.py`: error.log 改为按天轮转 + 含日期文件名，保留 30 天
- [ ] 1.3 `src/core/logging.py`: app.log 和 error.log 的 sink 均增加 `enqueue=True`
- [ ] 1.4 `src/core/logging.py`: 新增 `LOG_MAX_BODY = 10 * 1024 * 1024` 截断常量
- [ ] 1.5 `src/core/logging.py`: 新增 `SQL_SKIP_FULL_LOG`、`API_SKIP_FULL_LOG` 两个跳过名单常量

## 2. 响应处理器重命名 + 扩展

- [ ] 2.1 `src/middleware/response_envelope.py` → 重命名为 `response_processor.py`，函数名改为 `response_processor_middleware`
- [ ] 2.2 在响应包装后添加非 GET 请求的 `logger.info`，格式 `[API] {method} {path} | status={code} | data={data}`，对 `API_SKIP_FULL_LOG` 中的路由跳过 content（只记 status）
- [ ] 2.3 `src/main.py`: 更新所有引用（import + app.middleware 注册）
- [ ] 2.4 `tests/test_middleware.py`: 更新引用到 `response_processor_middleware`

## 3. MySQL SQL 返回值日志

- [ ] 3.1 `src/infra/db/mysql_db.py`: 从 `src.core.logging` 引入 `SQL_SKIP_FULL_LOG` 集合（不再自行定义）
- [ ] 3.2 `src/infra/db/mysql_db.py`: 添加 `_log_sql_result()` 辅助函数，含 _SKIP_FULL_LOG_METHODS 判断 + 序列化异常 try/except 兜底
- [ ] 3.3 `src/infra/db/mysql_db.py`: 为返回列表的方法（get_sessions、get_documents、get_all_kb）添加返回值日志
- [ ] 3.4 `src/infra/db/mysql_db.py`: 为返回单行的方法（get_session_by_id、get_user_by_account、get_user_by_token、get_kb_by_name）添加返回值日志
- [ ] 3.5 `src/infra/db/mysql_db.py`: `get_messages` 只记录 count+session_id，不记录 content
- [ ] 3.6 `src/infra/db/mysql_db.py`: `get_or_create_kb` 的两个返回路径均添加日志

## 4. ChromaDB 检索结果日志

- [ ] 4.1 `src/infra/db/vector_store.py`: similarity_search 添加返回值日志，格式 `[CHROMA] method=similarity_search | ...`
- [ ] 4.2 `src/infra/db/vector_store.py`: similarity_search_all 添加返回值日志
- [ ] 4.3 `src/infra/db/vector_store.py`: get_chunks_by_doc_id、get_chunks_paginated 添加返回值日志

## 5. 验证

- [ ] 5.1 `ruff check .` 无错误
- [ ] 5.2 `pytest tests/ -v` 全部通过
- [ ] 5.3 人工检查日志文件是否按预期输出（无控制台、app.log 含 trace data、error.log 只有 ERROR）
