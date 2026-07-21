# 数据流链路

## 链路 1：文档上传 → 解析 → 入库 ★

```
用户上传 → MinIO 存储 → 文档解析(parse) → 策略检测
→ 智能分块(chunk) → 分块质量校验 → [可选]分块质量评估
→ ChromaDB 向量入库 → MySQL 元数据更新(ready)
```

入口: `POST /api/kbs/documents/upload` → 后台 `asyncio.create_task(_process_document_task)`
文件类型: `.pdf` / `.docx` / `.txt`，异步返回 `202` + `doc_id`

## 链路 2：用户问答 → 检索 → 生成 ★

```
用户提问 → SSE 建立 → 检索(search) → 精排(rerank)
→ 流式生成(stream_answer) → 引用高亮(citations)
→ 对话历史持久化(Redis + MySQL)
```

入口: `GET /api/chat/stream?session_id=&kb_id=&query=`
输出: SSE 事件流 `status → token → citation → done`

## 链路 3：知识库管理

```
创建知识库 → MySQL get_or_create（名称去重）→ 返回 kb_id
列出知识库 → MySQL 查询 + 文档计数
删除知识库 → 软删文档 → ChromaDB 删集合 → 软删 KB 记录
```

入口: `POST /api/kbs` / `POST /api/kbs/list` / `POST /api/kbs/delete`

## 链路 4：会话管理 ★

```
列出会话 → MySQL 查询最近 50 条
查看消息 → MySQL 查询 session 消息历史
删除会话 → Redis 清理 → MySQL 事务删除 session + 消息
```

入口: `POST /api/sessions/list` / `POST /api/sessions/messages` / `POST /api/sessions/delete`

## 链路 5：认证

```
登录/注册 → MySQL 查用户 → 密码校验/自动注册 → Redis 存 token
校验 → Redis 查 token → 返回 user_id
登出 → Redis 删 token
匿名 → 生成 UUID → Cookie 持久化
```

入口: `POST /api/auth/login` / `POST /api/auth/verify` / `POST /api/auth/logout` / `POST /api/auth/anonymous`

## 链路 6：RAGAS 质量评估（CLI）

**子链路 6a — 测试集生成**：
```
MySQL 查元信息 → ChromaDB 取分块 → 脱敏 → 构建 KnowledgeGraph
→ transforms → 生成测试集 QA → 保存 JSON
```
命令: `python -m src.cli.eval_ragas --kb-id xxx --generate --size 20`

**子链路 6b — 评估执行**：
```
加载测试集 JSON → 初始化 RAGChain → 对每条 QA 检索+生成
→ RAGAS 四指标评分 → 保存 CSV + Markdown → 写入 eval_report 表
```
命令: `python -m src.cli.eval_ragas --kb-id xxx`（加 `--gate` 启用质量门禁）

## 链路 7：分块质量评估（嵌入在链路 1 中）★

```
文档分块后 → 结构完整性评分 → 语义断裂率(SBR) → 粒度变异系数(CV)
→ 综合评分 → 写入 meta_info.eval（仅记录，不阻塞）
```

由 `CHUNK_EVAL_ENABLED` 开关控制，`src/eval/chunk_scorer.py` 实现

## 链路 8：工具性链路

- **LLM 连通性测试**: `POST /api/llm/test` — 验证模型可用性
- **检索质量调试**: `python -m src.cli.check_retrieval --kb xxx --query "..."` — 纯检索+精排，不调 LLM
- **健康检查**: `GET /api/health` / `POST /api/config`

> ★ 标注的为关键链路
