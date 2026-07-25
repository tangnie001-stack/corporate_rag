# Deep Research: FastAPI 分层架构最佳实践与 Corporate RAG 评估

## Executive Summary

本报告通过调研 FastAPI 社区最佳实践（17k+ stars 的 fastapi-best-practices）、Clean Architecture 模式、生产级 RAG 系统架构，对当前 Corporate RAG 项目的架构进行评估。核心结论：**项目已具备合理的分层骨架（api → services → infra），但大量业务逻辑仍散落在 api 层，未下沉到 services 层。** 需要新增 AuthService、重构 document 上传流程，将 api/ 的 11 处违规调用清理干净。

## Key Findings

### 1. API 层职责：公认的"薄胶水层"标准

**来源**: [zhanymkanov/fastapi-best-practices (17k+ stars)](https://github.com/zhanymkanov/fastapi-best-practices), [Clean Architecture FastAPI](https://github.com/vanthao03596/clean-architecture-fastapi), [Production-Ready FastAPI 2026](https://dev.to/datanestdigital/production-ready-fastapi-project-structure-2026-guide-b1g)

业界共识：API 路由处理器应是"薄薄的一层胶水"：

| 该做 (SHOULD) | 不该做 (SHOULD NOT) |
|---|---|
| 参数校验 (Pydantic) | 业务逻辑 (if/else、状态机) |
| 依赖组装 (Depends 链式组合) | 密码哈希 / JWT 生成 |
| 调用 service 层方法 | 直接 ORM 查询 |
| 响应格式化 + 状态码设置 | 数据库事务管理 (commit/rollback) |
| 异常转发 → HTTPException | 直接操作 S3/MinIO 客户端 |

**一句话**: 路由里出现的任何 `if` 条件、任何 `db.query()`、任何 `jwt.encode()` 都可能是职责违反的信号。

### 2. Service 层（编排层）的定位

**来源**: [arXiv RAG Architecture Survey 2601.05264](https://arxiv.org/html/2601.05264), [Enterprise RAG Maturity](https://www.applied-ai.com/briefings/enterprise-rag-architecture/)

RAG 系统的 Service 层负责**业务规则 + 流程编排**，包括：
- **AuthService**: 登录流程（密码校验 → token 生成 → Redis 存储）、token 验证、登出
- **DocumentService**: 文档处理流水线（已正确实现 async process_document）
- **ChatService**: 已删除（由 AgentService 替代 ✅）
- **AppService**: 全局依赖组装 + 跨子 service 编排

**RAG 流水线的分层** (arXiv survey)：

```
编排层 (Orchestration)   ← AgentService + LangGraph
融合重排层 (Fusion)      ← rerank_results
检索层 (Retrieval)       ← search()
知识库层 (Knowledge)     ← VectorStore + MySQL
可信校准层 (Trust)       ← citation + grader loop
```

### 3. 认证模块的最佳实践

**来源**: [FastAPI Auth Guide (BetterStack)](https://betterstack.com/community/guides/scaling-python/authentication-fastapi/), [FastAPI OAuth2+JWT (官方)](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/)

认证在 FastAPI 项目中有两种组织方式：

**模式 A: 独立 domain 模块** (推荐)：

```
src/auth/
├── router.py        ← 登录/登出/验证端点
├── schemas.py       ← LoginRequest, TokenResponse
├── service.py       ← authenticate(), create_token(), verify()
├── dependencies.py  ← get_current_user, get_current_active_user
├── exceptions.py
└── utils.py         ← hash_password (可放在核心工具)
```

**模式 B: 跨切面**：

```
src/core/security.py  ← JWT + 密码工具 (纯函数)
src/services/auth_service.py  ← 业务逻辑
src/api/auth.py  ← 路由
```

当前项目 (`src/api/auth.py`) 的 12 行业务逻辑代码应全部下沉到 `services/auth_service.py`，api 层只保留 `Depends()` 组装。

### 4. 文件上传的分层处理

**来源**: [RAG Chatbot with FastAPI (futuresmart.ai)](https://blog.futuresmart.ai/building-a-production-ready-rag-chatbot-with-fastapi-and-langchain), [FastAPI S3 Upload Pattern](https://github.com/jimmygian/fastapi-s3-local)

生产级 RAG 文档上传的"双路径"架构：

```
上传路径 (Ingestion):
Upload API → Validate → MinIO存储 → DB记录 → 后台处理任务
                                              ↓ (async)
                                          解析 → 分块 → 向量化 → 入库

查询路径 (Query):
Question → AgentService.stream_chat() → LangGraph → Retrieve → Rerank → Generate
```

当前项目已经部分实现了这个模式，但问题在于：
- `api/documents.py` 的 upload handler 仍直接调 `FileStore`（MinIO 客户端）+ `svc.db.add_document()`（SQL）—— 这属于业务逻辑
- 应在 `DocumentService` 增加 `store_and_process()` 方法，将 MinIO 上传 + DB 记录 + 后台任务编排合并为一个 service 调用

### 5. 当前项目整体架构评估

```
src/
├── api/              ← ⚠️ 应纯路由，现混入业务逻辑
│   ├── auth.py       ← ❌ 调 UserAuth + Redis + svc.db (业务逻辑)
│   ├── documents.py  ← ❌ 调 FileStore (业务逻辑)
│   └── sessions.py   ← ❌ 调 chat_manager (绕过 service)
│
├── services/         ← ✅ 骨架正确，但功能不完整
│   ├── app_service.py  ← ✅ 依赖重组已完成
│   ├── agent_service.py ← ✅ LangGraph 编排
│   └── document_service.py ← ✅ 异步处理
│   └── ❌ 缺 auth_service.py
│
├── agents/           ← ✅ LangGraph 图
├── rag/              ← ⚠️ RAGChain 死代码待删
├── chat/             ← ✅ 纯 async
├── utils/            ← ✅ Phase 3 新建
├── infra/            ← ✅ 基础设施
└── config/           ← ✅ 配置
```

### 6. 对照业界标准的问题清单

| 问题 | 严重度 | 当前状态 | 业界推荐 |
|------|--------|---------|---------|
| auth 业务逻辑在 api 层 | P0 | `api/auth.py` 11行业务逻辑 | 创建 `services/auth_service.py` |
| 文件上传业务逻辑在 api 层 | P1 | `api/documents.py` 调 FileStore | 迁入 `DocumentService` |
| api/ 直接访问 `svc.db.*` | P1 | auth.py + kb_eval.py | 通过 service 封装 |
| api/ 直接访问 `svc.chat_manager.*` | P2 | sessions.py | 通过 AppService 委托 |
| 缺少 `services/auth_service.py` | P0 | 不存在 | 新增 |
| RAGChain 死代码 | P1 | 零生产调用 | 删除 |
| 文件超标 (883/602/597行) | P1 | mysql_db/vector_store/eval | 拆模块包 |
| 测试引用已删方法 | P0 | test_prompt/stream/tracing | 更新/删除测试 |

### 7. 推荐整改顺序

**Phase 4-a: service 层补全（P0）**
1. 创建 `services/auth_service.py`，封装 login/verify/logout 流程
2. `api/auth.py` 简化为纯路由
3. 更新测试

**Phase 4-b: 文件上传下沉（P1）**
4. DocumentService 增加 `store_and_process()` 方法（MinIO + DB + 后台任务）
5. `api/documents.py` 的 upload handler 简化为调 service

**Phase 4-c: 清理（P1）**
6. 删除 RAGChain 死代码
7. 删除 api/sse_utils.py 桥接层
8. 清理测试 mock

**Phase 4-d: 文件拆分（P1）**
9. mysql_db.py → 模块包
10. vector_store.py → 模块包

## Contrarian Views And Risks

- **"Domain-based 结构优于 layer-based 结构"**：fastapi-best-practices 推荐按 domain 组织（auth/、文档/等各自有自己的 router/service/schema），但当前项目按 layer 组织（api/、services/ 等）。迁移到 domain 结构是破坏性改动，当前项目规模下 layer-based 结构是可接受的折中。
- **"是否需要 Repository 模式"**：许多生产项目推荐在 service 层和 DB 之间再加一层 `repositories/`。当前项目直接让 service 调 `infra/db/mysql_db.py` 的方法，跳过了这一层。是否值得加取决于读写的复杂度增长趋势。
- **"auth 是否值独立 service"**：如果认证逻辑只涉及 3 个端点（login/verify/logout）且不会扩展，保留在 api/ 也有一定合理性。但从架构纯净度和可测试性来看，独立服务更有价值。

## Sources

1. [zhanymkanov/fastapi-best-practices](https://github.com/zhanymkanov/fastapi-best-practices) — FastAPI 项目最佳实践（17k+ stars）
2. [Production-Ready FastAPI Project Structure 2026](https://dev.to/datanestdigital/production-ready-fastapi-project-structure-2026-guide-b1g) — 三层次结构指南
3. [Clean Architecture FastAPI (vanthao03596)](https://github.com/vanthao03596/clean-architecture-fastapi) — 整洁架构参考实现
4. [FastAPI Official: OAuth2 + JWT](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/) — FastAPI 官方认证指南
5. [BetterStack: FastAPI Authentication Complete Guide](https://betterstack.com/community/guides/scaling-python/authentication-fastapi/) — 认证完整指南
6. [FastAPI Production Patterns 2025](https://orchestrator.dev/blog/2025-1-30-fastapi-production-patterns/) — 生产模式与 DI
7. [Enterprise RAG Architecture Guide](https://www.applied-ai.com/briefings/enterprise-rag-architecture/) — RAG 四层成熟度模型
8. [arXiv RAG Architecture Survey 2601.05264](https://arxiv.org/html/2601.05264) — RAG 系统五层架构调查
9. [LangGraph Architecture Analysis](https://blog.csdn.net/dongnihao/article/details/157614370) — LangGraph 思想-行动分离哲学
10. [FastAPI S3 Upload Pattern](https://github.com/jimmygian/fastapi-s3-local) — 文件上传分层示例
11. [RAG Chatbot with FastAPI (futuresmart.ai)](https://blog.futuresmart.ai/building-a-production-ready-rag-chatbot-with-fastapi-and-langchain) — FastAPI+LangChain RAG 项目结构
12. [FastAPI LLM Architecture Guide](https://markaicode.com/architecture/fastapi-llm-architecture/) — LLM 应用的 FastAPI 架构
13. [FastAPI Layered Architecture & DI](https://dev.to/markoulis/layered-architecture-dependency-injection-a-recipe-for-clean-and-testable-fastapi-code-3ioo) — 分层架构 + DI 模式
14. [LangGraph Agentic RAG Tutorial](https://github.langchain.ac.cn/langgraph/tutorials/rag/langgraph_agentic_rag/) — LangGraph Agentic RAG 实现
