"""SQL 查询语句集 — 集中管理所有数据库操作语句。

本模块包含 knowledge_base、document、conversation_history、sessions 四张表的
全部 CRUD 语句，每个常量以功能命名，便于 mysql_db.py 等业务模块引用。
SQL 语句集中管理而非散落各处的好处：
  1. 统一审查：一眼看到所有表结构和查询，方便 DBA review
  2. 避免重复：多个模块用到相同查询时不会写出两份不一致的 SQL
  3. 方便调试：有异常时可以快速定位到对应语句

命名规范：
  - 前缀：SELECT_* / INSERT_* / UPDATE_* / DELETE_* / CREATE_TABLE_*
  - 后缀：按查询维度命名（_BY_NAME / _BY_ID / _FOR_KB）
  - 所有占位符统一使用 PyMySQL 的 %s 风格
"""

# ====== 建表语句（init_db） ======
# 新建/更新表的逻辑在 MySQLDB.init_db() 中按顺序执行。
# 现有表通过 DROP + CREATE 重建（开发阶段允许），生产环境需迁移脚本。
# 所有 id 使用 VARCHAR(36) 存储 UUID，而非自增 INT：
#   - 分布式环境下不会冲突（后续可能多个 app 实例同时创建）
#   - 对外暴露的 API 中不易被枚举遍历
# 外键保留引用完整性，不再使用 ON DELETE CASCADE（改为应用层软删除）。

# 用户表。一条记录 = 一个注册用户。
# 密码存储的是 bcrypt 哈希值（空字符串代表未注册/游客）。
CREATE_TABLE_USERS: str = """\
CREATE TABLE IF NOT EXISTS users (
    id         VARCHAR(36)  PRIMARY KEY,
    account    VARCHAR(100) NOT NULL UNIQUE,
    password   VARCHAR(255) NOT NULL,
    token      VARCHAR(64),
    created_at DATETIME     DEFAULT CURRENT_TIMESTAMP
)
"""

# 知识库主表。一条记录 = 用户创建的一个"知识库"（如 "2024年年报"）。
# (user_id, name) 联合 UNIQUE 约束，保证同一用户下名称唯一。
CREATE_TABLE_KNOWLEDGE_BASE: str = """\
CREATE TABLE IF NOT EXISTS knowledge_base (
    id          VARCHAR(36)  PRIMARY KEY,
    user_id     VARCHAR(36)  NOT NULL DEFAULT '',
    name        VARCHAR(255) NOT NULL,
    description TEXT,
    status      VARCHAR(20)  NOT NULL DEFAULT 'active',
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_kb (user_id, name)
)
"""

# 文档元信息表。一条记录 = 用户上传的一个文件。
# status 追踪文档处理生命周期：pending → processing → ready / failed。
# chunk_count 在分块完成后回填，初始为 0。
# meta_info 存储 JSON 格式的扩展元数据（如 OCR 结果、解析参数等）。
CREATE_TABLE_DOCUMENT: str = """\
CREATE TABLE IF NOT EXISTS document (
    id                  VARCHAR(36)  PRIMARY KEY,
    user_id             VARCHAR(36)  NOT NULL DEFAULT '',
    kb_id               VARCHAR(36)  NOT NULL,
    filename            VARCHAR(255) NOT NULL,
    file_type           VARCHAR(10)  NOT NULL,
    file_size           INT          NOT NULL DEFAULT 0,
    file_path           VARCHAR(512),
    hash                VARCHAR(32),
    status              VARCHAR(20)  NOT NULL DEFAULT 'pending',
    processing_state    VARCHAR(20),
    processing_progress INTEGER      DEFAULT 0,
    processing_message  VARCHAR(255),
    error_msg           TEXT,
    chunk_strategy      VARCHAR(50)  DEFAULT 'parent_child',
    chunk_count         INTEGER      DEFAULT 0,
    meta_info           JSON,
    created_at          DATETIME     DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (kb_id) REFERENCES knowledge_base(id),
    INDEX idx_user_kb (user_id, kb_id)
)
"""

# 对话历史持久化表。与 sessions 表配合使用，存储消息内容。
# kb_id 用空字符串代表"所有知识库"，无 FK 约束。
CREATE_TABLE_CONVERSATION_HISTORY: str = """\
CREATE TABLE IF NOT EXISTS conversation_history (
    id                 INT          AUTO_INCREMENT PRIMARY KEY,
    session_id         VARCHAR(36)  NOT NULL,
    kb_id              VARCHAR(36)  NOT NULL DEFAULT '',
    role               ENUM('user','assistant') NOT NULL,
    content            TEXT         NOT NULL,
    sources            JSON,
    prompt_tokens      INT          DEFAULT 0,
    completion_tokens  INT          DEFAULT 0,
    total_tokens       INT          DEFAULT 0,
    model_name         VARCHAR(100) DEFAULT '',
    created_at         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_session (session_id, created_at)
)
"""

# 会话表。一条记录 = 用户的一次对话 session。
# kb_id 用空字符串代表"所有知识库"，无 FK 约束。
# user_id 用于区分不同用户的会话。
CREATE_TABLE_SESSIONS: str = """\
CREATE TABLE IF NOT EXISTS sessions (
    id          VARCHAR(36)  PRIMARY KEY,
    user_id     VARCHAR(36)  NOT NULL DEFAULT '',
    title       VARCHAR(20)  NOT NULL DEFAULT '',
    kb_id       VARCHAR(36)  NOT NULL DEFAULT '',
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user (user_id),
    INDEX idx_updated_at (updated_at DESC)
)
"""

# 修复遗留 FK 约束：旧版本 conversation_history 对 kb_id 有 FOREIGN KEY，
# 当 kb_id=''（所有知识库）时 INSERT 会失败。
# CREATE TABLE IF NOT EXISTS 不会修改已有表，因此需要单独 ALTER 修复。
DROP_CONVERSATION_HISTORY_FK: str = """\
ALTER TABLE conversation_history
DROP FOREIGN KEY IF EXISTS conversation_history_ibfk_1
"""

# ====== 知识库 CRUD ======

# 创建知识库记录。参数：[id, user_id, name, description]。
# 被 get_or_create_kb() 调用，先 INSERT，撞 (user_id, name) 联合 UNIQUE 约束则回退为 SELECT。
INSERT_KNOWLEDGE_BASE: str = """\
INSERT INTO knowledge_base (id, user_id, name, description) VALUES (%s, %s, %s, %s)
"""

# 按名称和用户查找知识库 ID。参数：[user_id, name]。
# 被 get_kb_by_name() 和 get_or_create_kb()（回退路径）调用。
# 返回 None 表示该名称不存在。
SELECT_KNOWLEDGE_BASE_ID_BY_NAME: str = """\
SELECT id FROM knowledge_base WHERE user_id = %s AND name = %s
"""

# 列出某用户的所有知识库（最近创建的在前）。参数：[user_id]。
SELECT_ALL_KNOWLEDGE_BASES: str = """\
SELECT k.id, k.user_id, k.name, COUNT(d.id) AS doc_count
FROM knowledge_base k
LEFT JOIN document d ON d.kb_id = k.id AND d.status != 'deleted'
WHERE k.user_id = %s AND k.status != 'deleted'
GROUP BY k.id, k.user_id, k.name
ORDER BY k.created_at DESC
"""

# 软删除知识库。参数：[kb_id]。标记为 deleted，保留记录（不再使用 ON DELETE CASCADE）。
SOFT_DELETE_KNOWLEDGE_BASE_BY_ID: str = """\
UPDATE knowledge_base SET status = 'deleted' WHERE id = %s
"""

# ====== 文档 CRUD ======

# 添加文档记录（状态由参数指定）。参数：[id, kb_id, user_id, filename, file_type, file_size,
#   status, file_path, hash, processing_state, processing_progress, processing_message,
#   chunk_strategy, meta_info]。
# 被 add_document() 调用。
INSERT_DOCUMENT: str = """\
INSERT INTO document (id, kb_id, user_id, filename, file_type, file_size, status, \
file_path, hash, processing_state, processing_progress, processing_message, \
chunk_strategy, meta_info)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

# 更新文档处理状态和分块数量（含新字段）。参数：[status, chunk_count, error_msg,
#   processing_state, processing_progress, processing_message, chunk_strategy, doc_id]。
UPDATE_DOCUMENT_STATUS: str = """\
UPDATE document SET status = %s, chunk_count = %s, error_msg = %s, \
processing_state = %s, processing_progress = %s, processing_message = %s, \
chunk_strategy = COALESCE(%s, chunk_strategy)
WHERE id = %s
"""

# 查询某知识库下的所有文档列表（含新字段）。参数：[kb_id]。
SELECT_DOCUMENTS_BY_KB_ID: str = """\
SELECT id, user_id, kb_id, filename, file_type, file_size, file_path, hash,
       status, processing_state, processing_progress, processing_message,
       error_msg, chunk_strategy, chunk_count, meta_info, created_at
FROM document WHERE kb_id = %s AND status != 'deleted' ORDER BY created_at DESC
"""

# 按 ID 查询文档。参数：[doc_id]。用于删除前校验文档存在、归属和状态。
SELECT_DOCUMENT_BY_ID: str = """\
SELECT id, user_id, kb_id, filename, file_type, file_size, file_path, hash,
       status, processing_state, processing_progress, processing_message,
       error_msg, chunk_strategy, chunk_count, meta_info, created_at
FROM document WHERE id = %s
"""

# 软删除文档。参数：[doc_id]。标记为 deleted，保留记录。
SOFT_DELETE_DOCUMENT: str = """\
UPDATE document SET status = 'deleted' WHERE id = %s
"""

# 软删除某知识库下的所有文档。参数：[kb_id]。
SOFT_DELETE_DOCUMENTS_BY_KB: str = """\
UPDATE document SET status = 'deleted' WHERE kb_id = %s
"""

# ====== 会话 CRUD ======

# 插入会话记录，首次消息时调用。
# 参数：[id, user_id, title, kb_id]。
INSERT_SESSION: str = """\
INSERT INTO sessions (id, user_id, title, kb_id) VALUES (%s, %s, %s, %s)
ON DUPLICATE KEY UPDATE updated_at = CURRENT_TIMESTAMP
"""

# 查询最近 50 条会话，包含知识库名称和消息数量。
# 按 updated_at 倒序排列，最新活跃的排在最前。
SELECT_SESSIONS: str = """\
SELECT s.id, s.title, s.kb_id, s.created_at, s.updated_at,
       COALESCE(kb.name, '所有知识库') AS kb_name,
       COUNT(ch.id) AS message_count
FROM sessions s
LEFT JOIN knowledge_base kb ON s.kb_id = kb.id AND s.kb_id != ''
LEFT JOIN conversation_history ch ON ch.session_id = s.id
GROUP BY s.id
ORDER BY s.updated_at DESC
LIMIT 50
"""

# 按 ID 查询单条会话。用于验证会话是否存在。
SELECT_SESSION_BY_ID: str = """\
SELECT id, title, kb_id, created_at, updated_at FROM sessions WHERE id = %s
"""

# 查询某会话的所有消息，按创建时间正序排列。
SELECT_MESSAGES_BY_SESSION: str = """\
SELECT role, content, sources, prompt_tokens, completion_tokens, total_tokens,
       model_name, created_at
FROM conversation_history
WHERE session_id = %s
ORDER BY created_at ASC
"""

# 删除会话记录。
DELETE_SESSION: str = """\
DELETE FROM sessions WHERE id = %s
"""

# 删除某会话的所有消息记录。
DELETE_MESSAGES_BY_SESSION: str = """\
DELETE FROM conversation_history WHERE session_id = %s
"""

# 插入单条消息到 conversation_history（含 token 用量与模型名）。
# 参数：[session_id, kb_id, role, content, sources, prompt_tokens, completion_tokens,
#        total_tokens, model_name]。
INSERT_MESSAGE: str = """\
INSERT INTO conversation_history (session_id, kb_id, role, content, sources, \
prompt_tokens, completion_tokens, total_tokens, model_name)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

# ====== 用户 CRUD ======

# 创建用户记录。参数：[id, account, password_hash]。
INSERT_USER: str = """\
INSERT INTO users (id, account, password) VALUES (%s, %s, %s)
"""

# 按账号查询用户信息。参数：[account]。
SELECT_USER_BY_ACCOUNT: str = """\
SELECT id, account, password, token, created_at FROM users WHERE account = %s
"""

# 更新用户 token。参数：[token, id]。
# 用户登录时生成 session token 并写入，用于后续请求的身份验证。
UPDATE_USER_TOKEN: str = """\
UPDATE users SET token = %s WHERE id = %s
"""

# 按 token 查询用户（登录态验证）。参数：[token]。
SELECT_USER_BY_TOKEN: str = """\
SELECT id, account FROM users WHERE token = %s
"""
