"""MySQL 元数据库模块 — 管理知识库和文档的元信息 CRUD 操作。

本模块封装了 aiomysql 的异步连接池与查询逻辑，提供：
  - 连接池管理（懒加载，自动回收和重连）
  - 建表初始化（幂等操作，多次调用安全）
  - 知识库（knowledge_base）的增删查
  - 文档（document）的增删查和状态更新
  - 对话历史的持久化存储（conversation_history 表）

数据模型关系：
  knowledge_base (1) ←→ (N) document
  knowledge_base (1) ←→ (N) conversation_history

在 RAG 系统中的角色：
  存储"哪个知识库叫什么名"、"哪些文档已上传"、"处理状态是什么"等元信息，
  向量数据存在 ChromaDB（vector_store.py），对话缓存存在 Redis（chat_manager.py）。
"""

import asyncio
import json
import uuid
from typing import Optional

import aiomysql
from loguru import logger

from src.core.logging import log_sql_result

from src.config import (
    MYSQL_HOST,
    MYSQL_PORT,
    MYSQL_USER,
    MYSQL_PASSWORD,
    MYSQL_DATABASE,
)
from src.config.queries import (
    CREATE_TABLE_CONVERSATION_HISTORY,
    CREATE_TABLE_DOCUMENT,
    CREATE_TABLE_KNOWLEDGE_BASE,
    CREATE_TABLE_SESSIONS,
    CREATE_TABLE_USERS,
    DELETE_KNOWLEDGE_BASE_BY_ID,
    DELETE_MESSAGES_BY_SESSION,
    DELETE_SESSION,
    DROP_CONVERSATION_HISTORY_FK,
    INSERT_DOCUMENT,
    INSERT_KNOWLEDGE_BASE,
    INSERT_MESSAGE,
    INSERT_SESSION,
    INSERT_USER,
    SELECT_ALL_KNOWLEDGE_BASES,
    SELECT_DOCUMENTS_BY_KB_ID,
    SELECT_DOC_NAMES_BY_IDS,
    SELECT_KB_NAME_BY_ID,
    SELECT_KNOWLEDGE_BASE_ID_BY_NAME,
    SELECT_MESSAGES_BY_SESSION,
    SELECT_SESSION_BY_ID,
    SELECT_SESSIONS,
    SELECT_USER_BY_ACCOUNT,
    SELECT_USER_BY_TOKEN,
    SOFT_DELETE_DOCUMENT_BY_ID,
    SOFT_DELETE_DOCUMENTS_BY_KB_ID,
    SOFT_DELETE_KNOWLEDGE_BASE_BY_ID,
    UPDATE_DOCUMENT_STATUS,
    UPDATE_USER_TOKEN,
)


class MySQLDB:
    """MySQL 数据库封装 — aiomysql 异步连接池版。

    异步连接池管理，自动回收和重连。
    所有方法均为 async def，调用方需 await。
    """

    def __init__(self):
        """初始化 MySQLDB，还不会立即创建连接池。

        连接池在首次调用 _get_pool() 时懒加载，
        或在 init_db() 中显式初始化。
        """
        self._pool: aiomysql.Pool | None = None
        self._pool_lock = asyncio.Lock()

    async def _get_pool(self) -> aiomysql.Pool:
        """获取或创建连接池（懒加载，线程安全）。"""
        if self._pool is not None:
            return self._pool
        async with self._pool_lock:
            if self._pool is not None:
                return self._pool
            self._pool = await aiomysql.create_pool(
                host=MYSQL_HOST,
                port=MYSQL_PORT,
                user=MYSQL_USER,
                password=MYSQL_PASSWORD,
                db=MYSQL_DATABASE,
                charset="utf8mb4",
                cursorclass=aiomysql.DictCursor,
                autocommit=True,
                minsize=2,
                maxsize=10,
                connect_timeout=10,
                pool_recycle=3600,  # 1 小时后回收连接，避免 MySQL 断开空闲连接
            )
            logger.info("MySQL connection pool created (minsize=2, maxsize=10)")
            return self._pool

    async def close(self) -> None:
        """关闭连接池，释放所有连接。"""
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None
            logger.info("MySQL connection pool closed")

    async def init_db(self) -> None:
        """创建所有业务表（幂等操作）。

        表结构：
          - users: 用户账号信息（id, account, password, token）
          - knowledge_base: 知识库元信息（id, user_id, name, description）
          - document: 文档元信息（id, user_id, kb_id, filename, status, chunk_count...）
          - conversation_history: 对话历史（session_id, role, content, sources, tokens）
          - sessions: 会话记录（id, user_id, title, kb_id）

        所有外键设置了 ON DELETE CASCADE，删除知识库时自动清理关联文档和历史记录。
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(CREATE_TABLE_USERS)
                await cursor.execute(CREATE_TABLE_KNOWLEDGE_BASE)
                await cursor.execute(CREATE_TABLE_DOCUMENT)
                await cursor.execute(CREATE_TABLE_CONVERSATION_HISTORY)
                await cursor.execute(CREATE_TABLE_SESSIONS)
                # 修复遗留 FK 约束：旧版 conversation_history 有 kb_id FK，
                # 新版用空字符串代表"所有知识库"，FK 已移除。
                # CREATE TABLE IF NOT EXISTS 不改已有表，单独 ALTER 修复。
                try:
                    await cursor.execute(DROP_CONVERSATION_HISTORY_FK)
                except Exception:
                    pass  # 表可能已无此 FK，忽略
            await conn.commit()
        logger.info("Database tables initialized")

    async def get_or_create_kb(
        self, user_id: str, name: str, description: str = ""
    ) -> tuple[str, bool]:
        """获取或创建知识库（原子操作，无 TOCTOU 竞态）。

        策略：先 INSERT，撞 (user_id, name) 联合 UNIQUE 约束则回退为 SELECT。
        (user_id, name) 联合 UNIQUE 约束保证原子性，
        避免了 CHECK-THEN-INSERT 模式的竞态窗口。

        Args:
            user_id: 用户 ID（空字符串代表无用户场景）
            name: 知识库名称（如 "2024年年报"）
            description: 知识库描述（可选）

        Returns:
            (kb_id, is_new) 元组：
            - kb_id: 知识库 UUID
            - is_new: True 表示新创建，False 表示已存在
        """
        pool = await self._get_pool()
        kb_id = str(uuid.uuid4())
        async with pool.acquire() as conn:
            try:
                async with conn.cursor() as cursor:
                    await cursor.execute(
                        INSERT_KNOWLEDGE_BASE, (kb_id, user_id, name, description)
                    )
                await conn.commit()
                log_sql_result("get_or_create_kb", INSERT_KNOWLEDGE_BASE.split("\n")[0].strip(), (kb_id, True))
                return kb_id, True
            except aiomysql.IntegrityError:
                # (user_id, name) 有联合 UNIQUE 约束：另一个请求已插入同名知识库
                # 回滚当前失败的 INSERT，释放连接上的事务状态
                await conn.rollback()
                existing_id = await self.get_kb_by_name(user_id, name)
                # 防御性断言：IntegrityError 表明数据一定存在
                if existing_id is None:
                    raise RuntimeError(
                        f"IntegrityError on '{name}' but get_kb_by_name returned None"
                    ) from None
                log_sql_result("get_or_create_kb", '', (existing_id, False))
                return existing_id, False

    async def add_document(
        self,
        doc_id: str,
        kb_id: str,
        filename: str,
        file_type: str,
        file_size: int,
        user_id: str = "",
        status: str = "pending",
        file_path: str | None = None,
        hash: str | None = None,
        processing_state: str | None = None,
        processing_progress: int = 0,
        processing_message: str | None = None,
        chunk_strategy: str = "parent_child",
        meta_info: str | None = None,
    ) -> None:
        """添加文档记录（状态初始为 pending）。

        文档上传时调用，先写入元信息，后续处理完成后再更新 status 和 chunk_count。

        Args:
            doc_id: 文档 UUID（调用方生成）
            kb_id: 所属知识库 ID
            filename: 原始文件名
            file_type: 文件类型（pdf / docx / txt）
            file_size: 文件大小（字节）
            user_id: 用户 ID（默认空字符串）
            status: 初始状态（默认 pending，也可指定 parsing 等）
            file_path: 文件存储路径
            hash: 文件 MD5 哈希
            processing_state: 处理阶段（如 chunking / vectorizing）
            processing_progress: 处理进度（0-100）
            processing_message: 处理状态描述消息
            chunk_strategy: 分块策略（默认 parent_child）
            meta_info: JSON 格式的扩展元数据
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    INSERT_DOCUMENT,
                    (
                        doc_id,
                        kb_id,
                        user_id,
                        filename,
                        file_type,
                        file_size,
                        status,
                        file_path,
                        hash,
                        processing_state,
                        processing_progress,
                        processing_message,
                        chunk_strategy,
                        meta_info,
                    ),
                )
            await conn.commit()
        logger.info(
            "SQL: {} | doc_id={} kb_id={} filename={} status={}",
            INSERT_DOCUMENT.split("\n")[0].strip(),
            doc_id,
            kb_id,
            filename,
            status,
        )

    async def create_session(
        self, session_id: str, title: str, kb_id: str, user_id: str = ""
    ) -> None:
        """创建或更新会话记录（幂等操作）。

        Args:
            session_id: 会话 ID
            title: 会话标题（截取首条消息前 20 字）
            kb_id: 关联的知识库 ID（空字符串代表"所有知识库"）
            user_id: 用户 ID（默认空字符串）
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    INSERT_SESSION, (session_id, user_id, title, kb_id)
                )
            await conn.commit()
        logger.info(
            "SQL: {} | session_id={} title={} kb_id={}",
            INSERT_SESSION.split("\n")[0].strip(),
            session_id,
            title,
            kb_id,
        )

    async def get_sessions(self) -> list[dict]:
        """返回最近 50 条会话列表，包含知识库名称和消息数量。

        Returns:
            字典列表，每项含 id, title, kb_id, kb_name, message_count, created_at, updated_at
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_SESSIONS)
                rows = await cursor.fetchall()
        log_sql_result("get_sessions", SELECT_SESSIONS.split("\n")[0].strip(), rows)
        return rows

    async def get_session_by_id(self, session_id: str) -> Optional[dict]:
        """按 ID 查询会话。

        Args:
            session_id: 会话 ID

        Returns:
            会话字典，不存在时返回 None
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_SESSION_BY_ID, (session_id,))
                row = await cursor.fetchone()
        log_sql_result("get_session_by_id", SELECT_SESSION_BY_ID.split("\n")[0].strip(), row)
        return row

    async def get_messages(self, session_id: str) -> list[dict]:
        """返回会话的所有消息，按 created_at 正序排列。

        Args:
            session_id: 会话 ID

        Returns:
            消息字典列表，每项含 role, content, sources, created_at
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_MESSAGES_BY_SESSION, (session_id,))
                rows = await cursor.fetchall()
        log_sql_result("get_messages", SELECT_MESSAGES_BY_SESSION.split("\n")[0].strip(), rows, session_id=session_id)
        return rows

    async def delete_session_and_messages(self, session_id: str) -> bool:
        """删除会话及其所有消息（同一事务内）。

        Args:
            session_id: 会话 ID

        Returns:
            True 表示会话存在且已删除，False 表示不存在
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(DELETE_MESSAGES_BY_SESSION, (session_id,))
                await cursor.execute(DELETE_SESSION, (session_id,))
                deleted = cursor.rowcount > 0
            await conn.commit()
        logger.info(
            "SQL: {} {} | session_id={} deleted={}",
            DELETE_MESSAGES_BY_SESSION.split("\n")[0].strip(),
            DELETE_SESSION.split("\n")[0].strip(),
            session_id,
            deleted,
        )
        return deleted

    async def save_message(
        self,
        session_id: str,
        kb_id: str,
        role: str,
        content: str,
        sources: Optional[list] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        model_name: str = "",
    ) -> None:
        """保存单条消息到 conversation_history。

        Args:
            session_id: 会话 ID
            kb_id: 关联的知识库 ID
            role: 角色（'user' 或 'assistant'）
            content: 消息内容
            sources: 来源引用列表（assistant 消息时使用）
            prompt_tokens: 提示 token 数
            completion_tokens: 补全 token 数
            total_tokens: 总 token 数
            model_name: 模型名称（如 qwen-plus）
        """
        pool = await self._get_pool()
        sources_json = json.dumps(sources, ensure_ascii=False) if sources else None
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    INSERT_MESSAGE,
                    (
                        session_id,
                        kb_id,
                        role,
                        content,
                        sources_json,
                        prompt_tokens,
                        completion_tokens,
                        total_tokens,
                        model_name,
                    ),
                )
            await conn.commit()
        logger.info(
            "SQL: {} | session_id={} role={} tokens={}",
            INSERT_MESSAGE.split("\n")[0].strip(),
            session_id,
            role,
            total_tokens,
        )

    async def update_document_status(
        self,
        doc_id: str,
        status: str,
        chunk_count: int = 0,
        error_msg: str = "",
        processing_state: str | None = None,
        processing_progress: int = 0,
        processing_message: str | None = None,
        chunk_strategy: str | None = None,
    ) -> None:
        """更新文档的处理状态和分块数量。

        文档解析完成后调用，将 status 从 pending 改为 ready，
        并记录实际的 chunk_count；解析失败则 status=failed，error_msg 记录原因。

        Args:
            doc_id: 文档 UUID
            status: 新状态（pending / processing / ready / failed）
            chunk_count: 实际分块数量
            error_msg: 失败时的错误信息
            processing_state: 处理阶段
            processing_progress: 处理进度（0-100）
            processing_message: 处理状态描述消息
            chunk_strategy: 分块策略（如 parent_child / qa / table_preserving）
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    UPDATE_DOCUMENT_STATUS,
                    (
                        status,
                        chunk_count,
                        error_msg,
                        processing_state,
                        processing_progress,
                        processing_message,
                        chunk_strategy,
                        doc_id,
                    ),
                )
            await conn.commit()
        logger.info(
            "SQL: {} | doc_id={} status={} chunks={}",
            UPDATE_DOCUMENT_STATUS.split("\n")[0].strip(),
            doc_id,
            status,
            chunk_count,
        )

    async def update_document_meta_info(self, doc_id: str, meta_info: dict) -> None:
        """更新文档的 meta_info JSON 列（用于存储分块评估结果）。

        Args:
            doc_id: 文档 UUID
            meta_info: 要写入的 JSON 可序列化字典
        """
        from src.config.queries import UPDATE_DOCUMENT_META_INFO

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                json_str = json.dumps(meta_info, ensure_ascii=False)
                await cursor.execute(UPDATE_DOCUMENT_META_INFO, (json_str, doc_id))
            await conn.commit()
        logger.info("SQL: UPDATE document meta_info | doc_id={}", doc_id)

    async def ensure_eval_report_table(self) -> None:
        """确保 eval_report 表存在（幂等）。"""
        from src.config.queries import CREATE_EVAL_REPORT_TABLE

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(CREATE_EVAL_REPORT_TABLE)
            await conn.commit()
        logger.info("SQL: CREATE TABLE IF NOT EXISTS eval_report")

    async def insert_eval_report(self, report: dict) -> None:
        """插入一条 RAGAS 评估报告。

        Args:
            report: 包含 kb_id, run_type, qa_count, faithfulness, answer_relevancy,
                    context_precision, context_recall, overall_score, passed,
                    report_path, triggered_by, detail_json 的字典
        """
        import uuid

        from src.config.queries import INSERT_EVAL_REPORT

        await self.ensure_eval_report_table()
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                detail_str = json.dumps(report.get("detail_json", []), ensure_ascii=False) if report.get("detail_json") else None
                await cursor.execute(INSERT_EVAL_REPORT, (
                    str(uuid.uuid4()),
                    report["kb_id"],
                    report.get("run_type", "manual"),
                    report["qa_count"],
                    report.get("faithfulness"),
                    report.get("answer_relevancy"),
                    report.get("context_precision"),
                    report.get("context_recall"),
                    report.get("overall_score"),
                    1 if report.get("passed") else 0,
                    report.get("report_path"),
                    report.get("triggered_by"),
                    detail_str,
                ))
            await conn.commit()
        logger.info("SQL: INSERT eval_report | kb_id={} run_type={}", report["kb_id"], report.get("run_type"))

    async def get_latest_eval_report(self, kb_id: str) -> dict | None:
        """获取知识库最新的 RAGAS 评估报告。

        Args:
            kb_id: 知识库 UUID

        Returns:
            dict 或 None（无评估记录时）
        """
        from src.config.queries import SELECT_LATEST_EVAL_REPORT

        await self.ensure_eval_report_table()
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_LATEST_EVAL_REPORT, (kb_id,))
                row = await cursor.fetchone()
        if row:
            return {
                "id": row[0], "kb_id": row[1], "run_type": row[2],
                "qa_count": row[3], "faithfulness": row[4],
                "answer_relevancy": row[5], "context_precision": row[6],
                "context_recall": row[7], "overall_score": row[8],
                "passed": bool(row[9]), "report_path": row[10],
                "triggered_by": row[11],
                "detail_json": json.loads(row[12]) if row[12] else None,
                "eval_date": row[13],
            }
        return None

    # ====== 用户 CRUD ======

    async def add_user(self, user_id: str, account: str, password_hash: str) -> None:
        """创建新用户记录。

        Args:
            user_id: 用户 UUID
            account: 登录账号
            password_hash: 密码的 bcrypt 哈希值
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(INSERT_USER, (user_id, account, password_hash))
            await conn.commit()
        logger.info(
            "SQL: {} | user_id={} account={}",
            INSERT_USER.split("\n")[0].strip(),
            user_id,
            account,
        )

    async def get_user_by_account(self, account: str) -> Optional[dict]:
        """按账号查询用户信息。

        Args:
            account: 登录账号

        Returns:
            用户字典（含 id, account, password, token, created_at），不存在返回 None
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_USER_BY_ACCOUNT, (account,))
                row = await cursor.fetchone()
        log_sql_result("get_user_by_account", SELECT_USER_BY_ACCOUNT.split("\n")[0].strip(), row, account=account)
        return row

    async def update_user_token(self, user_id: str, token: str) -> None:
        """更新用户 token（登录后生成 session token）。

        Args:
            user_id: 用户 UUID
            token: 新的 session token
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(UPDATE_USER_TOKEN, (token, user_id))
            await conn.commit()
        logger.info(
            "SQL: {} | user_id={}", UPDATE_USER_TOKEN.split("\n")[0].strip(), user_id
        )

    async def get_user_by_token(self, token: str) -> Optional[dict]:
        """按 token 查询用户（登录态验证）。

        Args:
            token: session token

        Returns:
            用户字典（含 id, account），不存在返回 None
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_USER_BY_TOKEN, (token,))
                row = await cursor.fetchone()
        log_sql_result("get_user_by_token", SELECT_USER_BY_TOKEN.split("\n")[0].strip(), row)
        return row

    # ====== 知识库 CRUD ======

    async def get_kb_by_name(self, user_id: str, name: str) -> Optional[str]:
        """根据用户 ID 和名称查找知识库 ID。

        Args:
            user_id: 用户 ID
            name: 知识库名称

        Returns:
            知识库 UUID，不存在时返回 None
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_KNOWLEDGE_BASE_ID_BY_NAME, (user_id, name))
                row = await cursor.fetchone()
        log_sql_result("get_kb_by_name", SELECT_KNOWLEDGE_BASE_ID_BY_NAME.split("\n")[0].strip(), row, user_id=user_id, name=name)
        return row["id"] if row else None

    async def get_kb_name_by_id(self, kb_id: str) -> Optional[str]:
        """根据知识库 UUID 查询知识库名称。

        Args:
            kb_id: 知识库 UUID

        Returns:
            知识库名称，不存在时返回 None
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_KB_NAME_BY_ID, (kb_id,))
                row = await cursor.fetchone()
        log_sql_result("get_kb_name_by_id", SELECT_KB_NAME_BY_ID.split("\n")[0].strip(), row, kb_id=kb_id)
        return row["name"] if row else None

    async def get_all_kb(self, user_id: str = "") -> list[dict]:
        """列出某用户的所有知识库（按创建时间倒序），含文档计数。

        Args:
            user_id: 用户 ID（默认空字符串）

        Returns:
            [{"id": str, "name": str, "doc_count": int}, ...] 列表
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_ALL_KNOWLEDGE_BASES, (user_id,))
                result = [
                    {
                        "id": row["id"],
                        "name": row["name"],
                        "doc_count": row["doc_count"],
                    }
                    for row in await cursor.fetchall()
                ]
            await conn.commit()  # 关闭隐式只读事务
        logger.info(
            "SQL: {} | user_id={} count={}",
            SELECT_ALL_KNOWLEDGE_BASES.split("\n")[0].strip(),
            user_id,
            len(result),
        )
        log_sql_result("get_all_kb", SELECT_ALL_KNOWLEDGE_BASES.split("\n")[0].strip(), result, user_id=user_id)
        return result

    async def delete_kb(self, kb_id: str) -> bool:
        """删除知识库及其关联的所有文档和对话历史（CASCADE）。

        Args:
            kb_id: 知识库 UUID

        Returns:
            True 表示删除成功，False 表示该知识库不存在
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(DELETE_KNOWLEDGE_BASE_BY_ID, (kb_id,))
                deleted = cursor.rowcount
            await conn.commit()
        logger.info(
            "SQL: {} | kb_id={} deleted={}",
            DELETE_KNOWLEDGE_BASE_BY_ID.split("\n")[0].strip(),
            kb_id,
            deleted > 0,
        )
        return deleted > 0

    async def get_documents(self, kb_id: str) -> list[dict]:
        """获取指定知识库下的所有文档列表（按创建时间倒序）。

        Args:
            kb_id: 知识库 UUID

        Returns:
            文档信息字典列表，每条包含 id, filename, status, chunk_count 等字段
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_DOCUMENTS_BY_KB_ID, (kb_id,))
                rows = await cursor.fetchall()
        log_sql_result("get_documents", SELECT_DOCUMENTS_BY_KB_ID.split("\n")[0].strip(), rows, kb_id=kb_id)
        return rows

    async def get_doc_names(self, doc_ids: list[str]) -> dict[str, str]:
        """根据文档 ID 列表查询对应的文件名。

        Args:
            doc_ids: 文档 UUID 列表

        Returns:
            {doc_id: filename} 字典，不存在的 ID 不包含在结果中
        """
        if not doc_ids:
            return {}

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                placeholders = ", ".join(["%s"] * len(doc_ids))
                sql = SELECT_DOC_NAMES_BY_IDS.format(placeholders)
                await cursor.execute(sql, doc_ids)
                rows = await cursor.fetchall()
        log_sql_result("get_doc_names", f"SELECT id, filename FROM document WHERE id IN ({len(doc_ids)} ids)", rows)
        return {row["id"]: row["filename"] for row in rows}

    async def soft_delete_document(self, doc_id: str) -> bool:
        """软删除文档（标记为 deleted 状态）。

        Args:
            doc_id: 文档 UUID

        Returns:
            True 表示删除成功，False 表示文档不存在或已删除
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SOFT_DELETE_DOCUMENT_BY_ID, (doc_id,))
                deleted = cursor.rowcount
            await conn.commit()
        logger.info(
            "SQL: {} | doc_id={} deleted={}",
            SOFT_DELETE_DOCUMENT_BY_ID.split("\n")[0].strip(),
            doc_id,
            deleted > 0,
        )
        return deleted > 0

    async def soft_delete_documents_by_kb(self, kb_id: str) -> int:
        """按知识库批量软删除所有关联文档。

        Args:
            kb_id: 知识库 UUID

        Returns:
            实际标记删除的文档数量
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SOFT_DELETE_DOCUMENTS_BY_KB_ID, (kb_id,))
                count = cursor.rowcount
            await conn.commit()
        logger.info(
            "SQL: {} | kb_id={} deleted={}",
            SOFT_DELETE_DOCUMENTS_BY_KB_ID.split("\n")[0].strip(),
            kb_id,
            count,
        )
        return count

    async def soft_delete_kb(self, kb_id: str) -> bool:
        """软删除知识库（标记 status 为 deleted）。

        Args:
            kb_id: 知识库 UUID

        Returns:
            True 表示删除成功
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SOFT_DELETE_KNOWLEDGE_BASE_BY_ID, (kb_id,))
                deleted = cursor.rowcount
            await conn.commit()
        logger.info(
            "SQL: {} | kb_id={} deleted={}",
            SOFT_DELETE_KNOWLEDGE_BASE_BY_ID.split("\n")[0].strip(),
            kb_id,
            deleted > 0,
        )
        return deleted > 0
