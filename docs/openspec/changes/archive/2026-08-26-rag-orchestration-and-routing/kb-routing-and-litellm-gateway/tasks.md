## 1. LiteLLM Proxy 部署

- [ ] 1.1 创建 `litellm/config.yaml`，配置 DashScope 和 DeepSeek 模型路由
- [ ] 1.2 在 `docker-compose.yml` 中添加 litellm-proxy 服务（含 mem_limit/restart/环境变量）
- [ ] 1.3 在 `.env` 中添加 `LITELLM_MASTER_KEY=sk-test-123456`
- [ ] 1.4 启动 Proxy：`docker compose up -d litellm-proxy`
- [ ] 1.5 验证：`curl localhost:4000/health` 返回 200
- [ ] 1.6 验证：`curl -H "Authorization: Bearer sk-test-123456" localhost:4000/v1/models` 返回模型列表

## 2. 模型配置重构

- [ ] 2.1 重构 `src/config/settings.py`：拆分出 `LLM_MODEL` / `LLM_API_KEY` / `LLM_BASE_URL` / `EMBEDDING_MODEL` / `EMBEDDING_API_KEY` / `EMBEDDING_BASE_URL` / `RERANK_MODEL` / `RERANK_API_KEY` / `LLM_KWARGS`，设置合理的默认值和 fallback（`LLM_API_KEY` → `DASHSCOPE_API_KEY`，`LLM_BASE_URL` 默认走 DashScope 直连）
- [ ] 2.2 重构 `src/models.py`：`get_llm()` 改用 `ChatOpenAI` 配置驱动；`get_embeddings()` 改用 `OpenAIEmbeddings` 配置驱动；保留 `get_rerank()` 不变但模型名改为配置读取
- [ ] 2.3 验证：不依赖 Proxy（`LLM_BASE_URL` 走 DashScope 直连），启动系统确认 LLM / Embedding / Rerank 正常
- [ ] 2.4 验证：切到 Proxy 地址（`LLM_BASE_URL=http://litellm-proxy:4000`），重启后确认链路正常

## 3. Rerank 模型升级

- [ ] 3.1 将 `settings.py` 中 `RERANK_MODEL` 默认值从 `gte-rerank-v1` 改为 `qwen3-rerank`
- [ ] 3.2 验证 Rerank 功能正常，检索质量不下降

## 4. 知识库智能路由

- [ ] 4.1 新增 `src/rag/kb_router.py`：`KBRouter` 类，实现语义路由（Embedding 相似度）和 LLM 兜底
- [ ] 4.2 修改 `src/config/queries.py`：`SELECT_ALL_KNOWLEDGE_BASES` SQL 增加 `k.description` 字段
- [ ] 4.3 修改 `src/infra/db/entities/kb.py`：`KbListItem` 增加 `description` 字段
- [ ] 4.4 修改 `src/infra/db/mysql_db/kb_repo.py`：`get_all_kb()` 返回 `description`
- [ ] 4.5 修改 `src/agents/graph/state.py`：添加 `_resolved_kb_ids: list[str] | None`
- [ ] 4.6 新增 `kb_router_node` 在 `src/agents/graph/nodes.py`，放在 workflow 入口
- [ ] 4.7 修改 `retrieve_node`：当 `_resolved_kb_ids` 有值时传入 `vector_store.similarity_search`（其已支持 `list[str]`）
- [ ] 4.8 修改 `src/agents/graph/workflow.py`：注册 `kb_router_node` 和对应的边
- [ ] 4.9 验证：选"所有知识库"后路由到正确 KB，可通过日志确认搜索范围缩小（只搜了 top-2 而非全部）
- [ ] 4.10 验证：低置信度查询触发 LLM 兜底，路由仍然有效
- [ ] 4.11 验证：路由未命中时降级为全量检索

## 5. 清理与兼容

- [ ] 5.1 检查所有旧配置项的引用，确保没有遗漏（如 `DASHSCOPE_API_KEY` 做 fallback）
- [ ] 5.2 更新 `.env.example` 反映新的配置结构
- [ ] 5.3 更新项目 README 中模型配置相关说明
