-- 为 sessions 增加 agent 列（本会话绑定的智能体预设名；'' = 未绑定）
-- 执行：docker exec -i corporate-rag-mysql mysql -uroot -pfinancial_qa_pass financial_qa < 本文件
ALTER TABLE sessions
  ADD COLUMN agent VARCHAR(64) NOT NULL DEFAULT ''
  COMMENT '会话绑定的智能体预设名（ASCII slug；空=未绑定）';
