# RAG 流水线优化（合并归档）

归档日期：2026-07-22

## 包含的 Change

1. **rag-qa-pipeline-logging** — RAG 问答链路 INFO 日志补全（检索/重排序/生成四段耗时）
2. **rag-system-improvements** — 三项优化：LLM 入口统一、CLI trace_id 注入、大表格行级切分+残差短文本合并
3. **tiny-chunk-merge** — Tiny chunk 自动合并（<50 tokens 碎片合并到前一个 chunk，仅 parent_child/table_preserving 策略）

## 共同主题
RAG 流水线各环节的日志、分块和基础设施优化。
