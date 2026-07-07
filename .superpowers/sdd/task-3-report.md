# Task 3 Report: MySQL 操作日志

## 修改文件
`src/infra/db/mysql_db.py`

## 添加日志的 7 个方法

| 方法 | 行号 | 日志内容 |
|------|------|---------|
| `add_document()` | 243-246 | `SQL add_document: doc_id={} kb_id={} filename={} status={}` |
| `get_document()` | 262 | `SQL get_document: doc_id={} found={}` |
| `soft_delete_document()` | 280 | `SQL soft_delete_document: doc_id={} rows_affected={}` |
| `soft_delete_documents_by_kb()` | 298 | `SQL soft_delete_documents_by_kb: kb_id={} rows_affected={}` |
| `soft_delete_kb()` | 589 | `SQL soft_delete_kb: kb_id={} found={}` |
| `get_documents()` | 606 | `SQL get_documents: kb_id={} count={}` |
| `update_document_status()` | 469-472 | `SQL update_document_status: doc_id={} status={} chunk_count={}` |

## 改动要点

- **`get_document()`**: `return await cursor.fetchone()` → 先赋值 `row`，log 后再 `return row`
- **`get_documents()`**: `return rows` 从 `async with pool.acquire()` 块内移出，log 后再返回
- **`soft_delete_document()`**, **`soft_delete_documents_by_kb()`**, **`soft_delete_kb()`**: 先提取 `rowcount`/`ok`，在 `async with` 块外 log 后再 return
- **`add_document()`**, **`update_document_status()`**: 在 `await conn.commit()` 后、方法结束前添加 logger.info

## 验证
- `ruff check src/infra/db/mysql_db.py` — All checks passed
