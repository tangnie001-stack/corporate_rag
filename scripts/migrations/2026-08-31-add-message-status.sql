-- 为 conversation_history 增加 status 列（complete/interrupted）
-- 执行：docker exec -i corporate-rag-mysql mysql -uroot -pfinancial_qa_pass financial_qa < 本文件
ALTER TABLE conversation_history
  ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'complete'
  COMMENT 'complete/interrupted';
