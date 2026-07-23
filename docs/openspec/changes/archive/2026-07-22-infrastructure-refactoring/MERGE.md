# 基础架构重构（合并归档）

归档日期：2026-07-22

## 包含的 Change

1. **codebase-refactoring** — 源码结构重构：rag_chain.py/chat_manager.py 拆包、api 目录扁平化、三层架构落地、测试目录镜像 src/
2. **di-refactor** — 依赖注入统一：6 个 API 模块的重复 _get_service() 合并为 FastAPI Depends，测试 mock 改为 dependency_overrides

## 共同主题
代码组织、架构分层和依赖管理的基础设施重构。
