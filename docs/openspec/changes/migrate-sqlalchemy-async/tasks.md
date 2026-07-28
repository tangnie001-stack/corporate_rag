## 1. 基础设施搭建

- [ ] 1.1 创建 `src/infra/db/base.py` — 沿用已有的 `Base(DeclarativeBase)`、`IDMixin`、`TimestampMixin`、`UTCDateTime`
- [ ] 1.2 创建 `src/infra/db/engine.py` — `create_async_engine("mysql+aiomysql://...")` + `async_sessionmaker(expire_on_commit=False)`
- [ ] 1.3 创建 `src/infra/db/models/__init__.py` — 导出所有 Model
- [ ] 1.4 搬迁 `entities/search.py` — 将 `ChunkResult`、`ChunkQueryResult` 移到 `src/infra/db/vector_store/types.py`
- [ ] 1.5 更新 `src/infra/db/__init__.py` — 删除 `from src.infra.db.mysql_db import MySQLDB`，改为空导出

## 2. ORM 模型定义

- [ ] 2.1 创建 `src/infra/db/models/user.py` — UserModel
- [ ] 2.2 创建 `src/infra/db/models/kb.py` — KbModel
- [ ] 2.3 创建 `src/infra/db/models/document.py` — DocModel
- [ ] 2.4 创建 `src/infra/db/models/chat.py` — SessionModel + MessageModel
- [ ] 2.5 创建 `src/infra/db/models/eval_report.py` — EvalReportModel

## 3. Alembic 配置

- [ ] 3.1 `alembic init alembic` + 编辑 `alembic.ini` 连接串
- [ ] 3.2 编辑 `alembic/env.py` 适配异步引擎（`run_async_migrations`）
- [ ] 3.3 生成第一版迁移脚本：`alembic revision --autogenerate -m "init models"`
- [ ] 3.4 审查生成的迁移脚本 DDL 是否正确

## 4. 重写 Repository

- [ ] 4.1 重写 `UserRepo` — 3 个方法，内部改为 ORM 查询
- [ ] 4.2 重写 `EvalRepo` — 2 个方法
- [ ] 4.3 重写 `KbRepo` — 6 个方法
- [ ] 4.4 重写 `ChatRepo` — 4 个方法
- [ ] 4.5 重写 `DocumentRepo` — 8 个方法
- [ ] 4.6 删除 Repo 文件中旧的 import（`from src.config.queries import ...`、`from src.infra.db.entities import ...`）

## 5. 服务层适配

- [ ] 5.1 修改 `AppService.__init__` — 参数从 `mysql_db` 改为 `session_factory`（默认 `None`，运行时懒导入 `engine.session_factory`）；删除 `.db` 属性
- [ ] 5.2 修改 `AppService.insert_eval_report` — 参数类型从 `EvalReportEntity` 改为 `EvalReportModel`，更新 import
- [ ] 5.3 更新 `AppService` 中 `ChunkQueryResult` 的 import 路径
- [ ] 5.4 修改 `src/api/dependencies.py` — 确认 `AppService()` 无参构造正常工作
- [ ] 5.5 修复 `src/cli/eval_ragas.py:587` — `svc.db` → `svc._kb_repo`
- [ ] 5.6 修改 `src/main.py lifespan` — 删除 `db.init_db()` 调用，替换为空 pass（Alembic 手动单独执行）

## 6. 更新引用方 import 路径（search types 搬迁）

- [ ] 6.1 `src/rag/retrieval.py` — 更新 `ChunkResult` import
- [ ] 6.2 `src/infra/search/bm25_index.py` — 更新 `ChunkResult` import
- [ ] 6.3 `src/infra/db/vector_store/__init__.py` — 更新 `ChunkResult`、`ChunkQueryResult` import
- [ ] 6.4 `src/infra/db/vector_store/search.py` — 更新 `ChunkResult`、`ChunkQueryResult` import
- [ ] 6.5 `src/agents/graph/state.py` — 更新 `ChunkResult` import
- [ ] 6.6 `src/services/app_service.py` — 更新 `ChunkQueryResult` import（已在 5.2 覆盖）

## 7. 清理旧代码

- [ ] 7.1 删除 `src/config/queries.py`
- [ ] 7.2 删除 `src/infra/db/entities/`（含 `__init__.py`、`user.py`、`kb.py`、`document.py`、`chat.py`、`eval_report.py`，保留的 `search.py` 已在 1.4 搬迁）
- [ ] 7.3 删除 `src/infra/db/mysql_db/pool.py`
- [ ] 7.4 创建 `scripts/clean_all_data.py`（MySQL DROP + Redis FLUSHDB + MinIO 清桶 + ChromaDB 删 collection）
- [ ] 7.5 创建 `scripts/README.md` 说明迁移和清理命令

## 8. 测试修复

- [ ] 8.1 重写 `tests/infra/db/test_mysql_db.py` — session_factory 模式
- [ ] 8.2 重写 `tests/conftest.py` — `mysql_db` fixture → `session_factory` fixture，修复 `svc.db` 调用
- [ ] 8.3 重写 `tests/reset_data.py` — engine 替代 MySQLDB
- [ ] 8.4 修改 `tests/services/test_app_service.py` — 去掉 9 处 `@patch("MySQLDB")`，entity → MagicMock
- [ ] 8.5 修改 `tests/services/test_auth_service.py` — UserEntity → MagicMock
- [ ] 8.6 修改 `tests/api/test_documents.py` — DocEntity → MagicMock
- [ ] 8.7 更新测试中 `ChunkResult`/`ChunkQueryResult` 的 import 路径（3 个测试文件）

## 9. 验证

- [ ] 9.1 `pytest tests/ -v` 全部通过
- [ ] 9.2 `ruff check .` 无错误
- [ ] 9.3 全量清理后启动服务：`python scripts/clean_all_data.py` → `docker compose up -d --build` → `docker compose exec app alembic upgrade head` → 手动验证（登录、建知识库、上传文档、问答）
- [ ] 9.4 验证 `src/api/ragas_generate.py` 中 `AppService()` 无参构造正常工作（代码无需改动，列入验证清单）
- [ ] 9.5 验证 `src/api/dependencies.py` 中 `AppService()` 无参构造正常工作（代码无需改动，列入验证清单）

## 10. 收尾

- [ ] 10.1 `git add -A && git commit -m "feat: migrate to sqlalchemy 2.0 async orm + alembic"`
- [ ] 10.2 输出 `git diff HEAD~1` 供 review
