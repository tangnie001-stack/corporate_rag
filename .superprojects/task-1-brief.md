# Task 1: 新增 DB 查询方法

## 需求

在 `src/infra/db/mysql_db.py` 中新增两个方法：

### 1. `get_kb_name_by_id(kb_id: str) -> Optional[str]`

- 功能：根据知识库 UUID 查询知识库名称
- SQL: `SELECT name FROM knowledge_base WHERE id = %s`
- 需在 `src/config/queries.py` 中新增对应的 SQL 常量 `SELECT_KB_NAME_BY_ID`
- 返回知识库名称，不存在时返回 None

### 2. `get_doc_names(doc_ids: list[str]) -> dict[str, str]`

- 功能：根据文档 ID 列表查询对应的文件名
- SQL: 使用 `IN (...)` 查询 `SELECT id, filename FROM document WHERE id IN (%s, %s, ...)`
- 需在 `src/config/queries.py` 中新增对应的 SQL 常量 `SELECT_DOC_NAMES_BY_IDS`
- 返回 `{doc_id: filename}` 字典
- 如果 doc_ids 为空则返回空字典，不执行 SQL
- 注意：MySQL AIOMysql 的 `cursor.execute()` 不支持直接传 list 做 IN 参数，
  需要构建带 `%s` 占位符的 IN 子句

两个方法都是 async 方法，放在已有的 `MySQLClient` 类中。
调用方式和异常处理与同类中已有的方法保持一致（使用 `self._get_pool()` 获取连接池）。

## 完成标准

- 两个方法实现正确，类型标注完整
- 有对应的 SQL 常量在 `queries.py`
- `pytest tests/ -v` 至少通过已有的 MysqlDB 测试
