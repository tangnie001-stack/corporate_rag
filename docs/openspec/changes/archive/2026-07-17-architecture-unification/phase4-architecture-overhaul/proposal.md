## Why

项目在分块策略、文件持久化、用户隔离和检索质量上存在多个断点：父块上下文从未真正送入 LLM、分块策略单一无法适配不同类型文档、上传文件不持久化（容器重启丢失）、缺乏用户系统导致数据混在一起无法区分。

## What Changes

### 用户系统（新增）
- 注册登录合一，Token 存 Redis + MySQL，TTL 30 天
- `/api/kbs/*` 强制 token 校验，`/api/chat/*` 可选（匿名用户由后端 Set-Cookie `user_id` 标识）
- 所有核心表加 `user_id` 字段实现数据隔离

### MinIO 文件持久化（新增）
- 上传文件从 tempfile 改为存 MinIO，路径 `documents/{user_id}/{kb_id}/{doc_id}/{filename}`
- 上传流程：MinIO 先写 → 成功后才 INSERT MySQL，失败无脏数据
- 前端「正在同步上传中...」加载框

### 分块策略路由（重构）
- 从单一 `ParentChildChunker` 改为三层路由：扩展名 → 解析器，结构特征 → 分块策略
- 三种策略：`qa`（FAQ 问答集）、`table_preserving`（含表格的财务文档）、`parent_child`（通用兜底）
- `chunk_strategy` 写入 MySQL + ChromaDB metadata

### 分块数据结构增强
- chunk content 注入标题前缀（`【利润表 > 主要项目】`）
- metadata 新增 `heading_path`、`parent_content`（修复写入 ChromaDB 时被丢弃的问题）、`tokens`、`entities`（预留）
- `add_chunks()` 保留所有传入 metadata（不再丢弃）

### 检索链路修复
- 命中子块 → `parent_content` 有值则送父块给 LLM，无值（qa 策略）送子块
- Citation 事件同步展示父块 snippet
- **BREAKING**: `/chunks` API 返回格式从扁平数组改为 `{items, total, page, page_size}`

### 对话 Token 记录
- `conversation_history` 加 `prompt_tokens`、`completion_tokens`、`total_tokens`、`model_name`
- DashScope 响应中提取 usage 写入

## Capabilities

### New Capabilities
- `user-auth`: 用户注册/登录/Token 校验/匿名机制
- `file-storage`: MinIO 文件上传/下载/存储路径管理
- `chunking-strategy`: 三层分块策略路由（qa / table_preserving / parent_child）
- `chunk-retrieval`: 检索链路修复 + parent_content 上下文扩展

### Modified Capabilities
- `retrieval-quality`: 检索结果需要返回 parent_content 作为 LLM 上下文

## Impact

| 影响范围 | 说明 |
|---------|------|
| 新增文件 | `src/infra/file_store.py`、`src/infra/user_auth.py`、`src/api/routes/auth.py`、`src/api/middleware.py`、`nginx/html/login.html` |
| 修改文件 | 5 个后端模块 + 3 个前端页面 + 数据库初始化脚本 |
| 数据库 | `users` 表新增；`knowledge_base`、`document`、`sessions` 加 `user_id`；`document` 加 10 个字段；`conversation_history` 加 4 个字段 |
| 依赖 | `pyproject.toml` 加 `minio` 包 |
| API 变更 | `/api/kbs/*` 需 token；`/chunks` 返回格式变化 |
