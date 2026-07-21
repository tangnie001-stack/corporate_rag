# Task 1 Report: 新增 DB 查询方法

## 实现内容

在 `MySQLDB` 类中新增了两个 async 方法：

### 1. `get_kb_name_by_id(kb_id: str) -> Optional[str]`
- 根据知识库 UUID 查询名称
- SQL: `SELECT name FROM knowledge_base WHERE id = %s`
- 不存在时返回 None

### 2. `get_doc_names(doc_ids: list[str]) -> dict[str, str]`
- 根据文档 ID 列表查询文件名
- 使用动态 IN 子句（`%s` 占位符）
- 空列表返回空字典，不执行 SQL
- 返回 `{doc_id: filename}` 字典

## 测试结果

```
tests/infra/db/test_mysql_db.py::test_get_kb_name_by_id PASSED
tests/infra/db/test_mysql_db.py::test_get_doc_names PASSED
```

4/4 passed in focused test (2 existing + 2 new), output pristine.

## 文件变更

| 文件 | 操作 |
|------|------|
| `src/config/queries.py` | 新增 `SELECT_KB_NAME_BY_ID` 和 `SELECT_DOC_NAMES_BY_IDS` |
| `src/infra/db/mysql_db.py` | 新增 import + `get_kb_name_by_id()` + `get_doc_names()` |
| `tests/infra/db/test_mysql_db.py` | 新增 `test_get_kb_name_by_id` 和 `test_get_doc_names` |

## 自审发现

- 任务 brief 中提到的类名为 `MySQLClient`，实际代码中是 `MySQLDB`，方法已正确添加到 `MySQLDB` 类
- 遵循了现有代码模式：使用 `self._get_pool()` 获取连接池、`log_sql_result` 记录日志、`cursor.fetchone()`/`fetchall()` 获取结果
- 异常处理模式：与同类方法一致（无额外异常捕获，遵循透传型模式）

## 关注点

无。实现符合预期，测试覆盖正常路径和边界情况。

## 完整测试套件状态

229 passed, 30 failed (all pre-existing failures, none related to my changes).
MySQL DB tests: 4/4 passed.
