## Context

项目当前处于 Phase 3（检索增强）完成后的迭代阶段。用户上传文件存 tempfile 不持久化，分块策略单一固定为 ParentChild，检索链路中父块上下文被 `add_chunks()` 静默丢弃，且缺乏用户系统导致所有数据未隔离。本次改造覆盖存储架构、分块策略、检索链路、用户系统四大块。

## Goals / Non-Goals

**Goals:**
- 引入 MinIO 文件持久化，消除容器重启丢文件问题
- 实现三层分块策略路由，适配不同文档类型
- 修复 Parent-Child 检索链路，父块上下文真正送入 LLM
- 新增用户系统，实现数据隔离和 Token 校验
- 对话 Token 用量记录

**Non-Goals:**
- 不实现'所有知识库'搜索的 user_id 隔离（DB 层已预留 user_id，搜索逻辑后续加）
- 不做用户注销/删号功能
- 匿名会话不迁移到登录后账户
- 不做实体关联提取（metadata 预留 entities 字段）
- 不改 BM25 索引和 ChromaDB collection 命名规则
- 不做前端重写（仅增量修改现有 HTML/CSS）
- 不支持 OCR（扫描件检测已有，仅告警）

## Decisions

### 1. MinIO 先写后入库
文件先写入 MinIO，成功后才 INSERT MySQL 记录。避免脏数据，代价是 API 响应多了 MinIO 写入耗时，但同网络下可接受。

### 2. 分块路由三层递进 + block_type 标记
文件扩展名 → 选择解析器（已有）。解析时 parser 给每个 chunk 加 metadata.block_type="text"/"table"。解析后按问句密度（句子比例）和 block_type 检测，经 ChunkRouter 选策略。qa > table_preserving > parent_child 优先级。TablePreservingChunker 读取 block_type 标记做表格边界保护。

### 3. chunk metadata 保留所有字段
`add_chunks()` 不再重建 metadata dict，改为原样传入 + 补充必要字段（chunk_index, chunk_total）。修复 parent_content、tokens 等字段被丢弃的问题。

### 4. Token 校验范围
`/api/kbs/*` 强制校验，`/api/chat/*` 可选，`/api/auth/*` 不校验。匿名用户由后端 Set-Cookie `user_id` 标识，`token` Cookie 优先于 `user_id` Cookie。全库搜索的 user_id 隔离已预留字段，当前不实现。

### 5. 匿名用户机制
后端在首次无身份请求时生成 UUID 写入 `Set-Cookie: user_id=<uuid>; path=/; max-age=31536000`。登录后 `token` Cookie 替代 `user_id` Cookie。匿名会话历史不迁移。

### 6. Token 用量从 DashScope 流响应提取
`_stream_answer()` 从 DashScope 流式响应的最后一条 chunk 读取 `usage_metadata`，替代当前的启发式估算 `_estimate_usage()`。model_name 从配置的 `LLM_MODEL` 环境变量取值。

## Risks / Trade-offs

| 风险 | 缓解措施 |
|------|---------|
| MinIO 不可用导致上传全挂 | 上传接口超时 + 前端 loading 超时提示；MinIO 独立容器，不影响已存数据读取 |
| chunk_strategy 路由误判 | 匹配规则可配置，支持按文件名关键词或内容特征检测；误判时影响的是检索质量而非可用性 |
| token 存 Redis 增加运维复杂度 | Redis 已在用（对话缓存），复用现有实例 |
| 匿名用户 Cookie 无法跨设备 | 这是有意的设计——匿名不等于账户，跨设备需要登录 |
