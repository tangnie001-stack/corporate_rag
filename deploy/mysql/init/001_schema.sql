-- New table: users
CREATE TABLE IF NOT EXISTS users (
    id         VARCHAR(36)  PRIMARY KEY,
    account    VARCHAR(100) NOT NULL UNIQUE,
    password   VARCHAR(255) NOT NULL,
    token      VARCHAR(64),
    created_at DATETIME     DEFAULT CURRENT_TIMESTAMP
);

-- Recreate tables with new fields
DROP TABLE IF EXISTS conversation_history;
DROP TABLE IF EXISTS sessions;
DROP TABLE IF EXISTS document;
DROP TABLE IF EXISTS knowledge_base;

CREATE TABLE knowledge_base (
    id          VARCHAR(36)  PRIMARY KEY,
    user_id     VARCHAR(36)  NOT NULL DEFAULT '',
    name        VARCHAR(255) NOT NULL,
    description TEXT,
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_kb (user_id, name)
);

CREATE TABLE document (
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
    FOREIGN KEY (kb_id) REFERENCES knowledge_base(id) ON DELETE CASCADE,
    INDEX idx_user_kb (user_id, kb_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE sessions (
    id          VARCHAR(36)  PRIMARY KEY,
    user_id     VARCHAR(36)  NOT NULL DEFAULT '',
    title       VARCHAR(20)  NOT NULL DEFAULT '',
    kb_id       VARCHAR(36)  NOT NULL DEFAULT '',
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user (user_id),
    INDEX idx_updated_at (updated_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE conversation_history (
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
