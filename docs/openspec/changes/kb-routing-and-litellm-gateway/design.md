## Context

当前系统是一个企业级 RAG 知识库系统，后端基于 FastAPI + LangGraph + LangChain 构建。用户可以选择单个知识库检索，也可以选择"所有知识库"进行跨库检索。

当前 "所有知识库" 的实现（`src/rag/retrieval.py`）是获取用户的所有知识库后全部检索，存在检索噪声大、成本高的问题。同时大模型目前只支持阿里云 DashScope 一家，缺乏多 Provider 切换能力。

经过调研（参考 `docs/openspec/changes/kb-routing-and-litellm-gateway/plan.md`），决定引入 LiteLLM Proxy 作为统一的模型网关，并实现知识库智能路由。

## Goals / Non-Goals

**Goals:**
- LiteLLM Proxy 部署到 Docker Compose，本地无 DB 模式最小成本运行
- LLM / Embedding / Rerank 三类模型独立配置，全部从 env 读取
- 应用代码通过 `ChatOpenAI` / `OpenAIEmbeddings` + `base_url` 指向 Proxy
- 用户选"所有知识库"时，先语义路由匹配 KB，低置信度时 LLM 兜底
- Rerank 模型从 `gte-rerank-v1` 升级为 `qwen3-rerank`

**Non-Goals:**
- 不实现智能路由策略自动切换（Phase 3 再做）
- 不做大规模 KB（>100）场景的性能优化（留注释）
- 不引入 LiteLLM SDK，只用 Proxy 的 OpenAI 兼容接口
- 不修改已有文档的向量数据，不重新索引

## Decisions

### D1: LiteLLM Proxy 无 DB 模式本地部署
- **决策**：本地开发用无 DB 模式，通过 `config.yaml` 配置 Provider，`LITELLM_MASTER_KEY` 做请求鉴权
- **理由**：无 DB 模式资源占用低（~150MB），部署简单。生产环境需 PostgreSQL 启用虚拟 Key/预算/监控等高级功能
- **生产升级**：只加 `DATABASE_URL` 环境变量和换 `litellm-database` 镜像，config.yaml 不变

### D2: 应用代码直接用 LangChain OpenAI 兼容类
- **决策**：`models.py` 使用 `ChatOpenAI` 和 `OpenAIEmbeddings`，不引入 LiteLLM SDK
- **理由**：Proxy 暴露的是标准 OpenAI 兼容 API，`ChatOpenAI` 和 `ChatLiteLLM` 功能等价，少一层依赖
- **切换成本**：本地和环境切换只改 `base_url` + `model`，代码零改动

### D3: Embedding 固定 DashScope
- **决策**：Embedding 模型配置成可切换的，但实际生产不建议切换（存量数据向量不兼容）
- **理由**：ChromaDB 中的向量由原 Embedding 模型产出，切换模型会导致 cosine similarity 失效，需要全部 re-index
- **场景**：新项目可以从头指定，已有项目切 Embedding 需评估 re-index 成本

### D4: Rerank 独立走 DashScope
- **决策**：Rerank 固定调用 DashScope Rerank API，不经过 LiteLLM Proxy
- **理由**：LiteLLM Proxy 不支持 DashScope 的 `/rerank` 端点（仅支持 Cohere / Jina AI / Together AI 等海外提供商），而 DashScope 的 Rerank API 稳定可靠
- **Key 管理**：`RERANK_API_KEY` fallback 到 `DASHSCOPE_API_KEY`（而非 `LLM_API_KEY`），因为 Proxy 模式下 `LLM_API_KEY` 是 LiteLLM master key，DashScope 不识别

### D5: KB 路由用语义路由 + LLM 兜底
- **决策**：新 `kb_router_node` 插入在 workflow 的入口（classify 之前）
- **流程**：kb_id 非空 → 穿透；kb_id 为空 → 取用户 KB 列表 → Embedding 算相似度 → top-2 匹配 → 低置信度 LLM 分类
- **Embedding 来源**：路由 Embedding 和检索用的 Embedding 不共享，路由直接用当前系统嵌入模型实时计算
- **依赖注入**：沿用现有工厂函数模式，`make_kb_router_node(embed_fn, llm)` 注入 Embedding 和 LLM 依赖
- **架构位置**：workflow: `entry → kb_router → classify → ... → retrieve_node(使用resolved_kb_ids)`
- **注**：`VectorStore.similarity_search()` 已支持 `kb_ids: str | list[str]`，多 KB 并行搜索结果自动合并排序，`retrieve_node` 无需修改搜索逻辑

### D6: model_gateway 和 model_config 合并为 LiteLLM 配置
- **决策**：`model-gateway` 和 `model-config` 两个 capability 合并，因为模型网关的配置本身就是"配置驱动"的核心机制，实际代码量很小
- **理由**：LiteLLM Proxy 做路由，`.env` 做配置，各自的职责边界清晰，不需要拆成两个独立 spec

### D7: Proxy 不可用时的兜底
- **决策**：`LLM_BASE_URL` 和 `EMBEDDING_BASE_URL` 允许配置为直连地址，不强制依赖 Proxy
- **理由**：本地开发可能只启动 app 而不启动整个 docker-compose，如果 Proxy 不可用时系统完全不可用影响开发体验
- **默认值**：`LLM_BASE_URL` 默认指向 `http://litellm-proxy:4000`（Proxy 是标准路径）；开发者想跳过 Proxy 时手动改为直连地址：`https://dashscope.aliyuncs.com/compatible-mode/v1`
- **用法**：项目根目录 `docker compose up -d` 拉起所有服务（含 Proxy）→ 开箱即用；不想用 Proxy 时单独配 `.env`

### D8: Embedding 向量维度风险
- **决策**：`OpenAIEmbeddings` 不传 `dimensions` 参数，依赖模型默认输出维度
- **风险**：切换 Embedding Provider 时，若输出维度与 ChromaDB collection 创建时指定的维度不一致，查询会抛异常
- **规避**：不切换 Embedding Provider 则无此风险；切换时需对应重建 ChromaDB collection（re-index）
- **相关**：此风险仅影响切换 Embedding Provider 的场景，默认 DashScope 链路不受影响

### D9: 配置向后兼容
- **决策**：旧配置项 `DASHSCOPE_API_KEY` 作为所有新 Key 配置的 fallback
- **优先级**：`LLM_API_KEY` > `DASHSCOPE_API_KEY`；`EMBEDDING_API_KEY` > `LLM_API_KEY` > `DASHSCOPE_API_KEY`
- **效果**：只有旧 `.env` 的用户系统可正常启动，新 `.env` 用户逐个覆盖精细化配置
- **不推荐混合**：新旧配置同时存在时以新配置为准，不告警

### D10: config.yaml 模型前缀格式
- **决策**：不同 Provider 使用对应的 LiteLLM 前缀：
  - DashScope LLM/Embedding（兼容模式）→ `openai/qwen3.7-max` / `openai/qwen3.7-text-embedding`
  - DeepSeek LLM（原生模式）→ `deepseek/deepseek-v4-pro`
  - DeepSeek Embedding（OpenAI 兼容模式）→ `openai/deepseek-embed-v4`
- **理由**：`openai/` 前缀用于所有 OpenAI 兼容 API 的提供商，`deepseek/` 前缀启用 DeepSeek 原生能力（如 thinking 参数）
- **来源**：参考 LiteLLM 官方 config.yaml 文档

## Risks / Trade-offs

- **[风险] Proxy 单点故障**：LiteLLM Proxy 挂了 → 所有 LLM/Embedding 调用不可用。本地开发 `restart: unless-stopped` 保证自动恢复，生产需高可用部署
- **[风险] Proxy 配置变更需要重启**：config.yaml 改 Provider 需要 `docker compose restart litellm-proxy`。生产方案可通过 Proxy 后台热更新 config.yaml 避免重启（需 DB 模式）
- **[风险] 路由 Embedding 实时计算延迟**：少量 KB（<20 个）可接受；超过 100 个需引入 FAISS 索引缓存 KB 向量。当前留 TODO 注释
- **[权衡] Rerank Key 管理**：切换到 DeepSeek 后需要两个 API Key（LLM: DeepSeek, Rerank: DashScope），用户需额外管理。已在 settings.py 做 fallback 降低心智负担
- **[权衡] KB 路由准确性**：语义路由依赖 KB 的 name + description 质量。如果 KB 描述为空或过于模糊，路由会退化到 LLM 兜底或全量检索
- **[风险] Embedding 维度不匹配**：切换 Embedding Provider 时若新模型输出维度与已有 ChromaDB collection 不一致，查询会抛异常。默认 DashScope 链路无此风险
- **[风险] Proxy 配置未经验证**：LiteLLM Proxy 对 `openai/` 前缀 + 自定义 `api_base` 的兼容性未实际验证。tasks 中增加验证步骤：启动后调用 `/v1/models` 确认路由正常

## Migration Plan

1. **Phase 1**：Docker Compose 加入 LiteLLM Proxy，调整 `models.py` 指向 Proxy，验证 DashScope 链路正常
2. **Phase 2**：实现 `kb_router.py` 并接入 workflow，验证智能路由逻辑
3. **Phase 3**：升级 Rerank 模型为 qwen3-rerank
4. **Phase 4**：清理旧的配置项和处理兼容性

Rollback 策略：对于 `models.py` 改动，保留旧的 `get_llm()` / `get_embeddings()` 函数签名的注释，更换 `.env` 指向原来的直连地址即可回退。
