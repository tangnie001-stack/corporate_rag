"""ORM metadata 必须能被 PostgreSQL 方言渲染，且带上查询路径索引。

这两条是 Task 5 用 autogenerate 生成 baseline 的前提：
方言类型会在渲染期直接失败；未声明的索引不会被建立、还会在下次 autogenerate 时被 drop。
"""

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

import src.infra.db.models  # noqa: F401  —— 必须先 import 才会填充 Base.metadata
from src.infra.db.base import Base

# 旧 MySQL schema 里服务于查询路径的索引（名称为保持可追溯而沿用旧名）
EXPECTED_INDEXES = {
    "idx_user_kb": ("document", ("user_id", "kb_id")),
    "idx_session": ("conversation_history", ("session_id", "created_at")),
    "idx_user": ("sessions", ("user_id",)),
    "idx_updated_at": ("sessions", ("updated_at",)),
    "idx_kb_date": ("eval_report", ("kb_id", "eval_date")),
}


@pytest.mark.parametrize("table_name", sorted(Base.metadata.tables))
def test_every_table_compiles_under_postgresql(table_name):
    """任何 MySQL 方言类型都会在渲染期抛出 CompileError。"""
    table = Base.metadata.tables[table_name]
    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "CREATE TABLE" in ddl


@pytest.mark.parametrize("index_name", sorted(EXPECTED_INDEXES))
def test_query_path_index_is_declared(index_name):
    table_name, columns = EXPECTED_INDEXES[index_name]
    table = Base.metadata.tables[table_name]
    found = [ix for ix in table.indexes if ix.name == index_name]
    assert found, f"{table_name} 缺少索引 {index_name}"
    assert tuple(c.name for c in found[0].columns) == columns
