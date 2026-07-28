## Why

当前系统在用户选择"所有知识库"时，直接全量检索所有 KB，检索噪声大、成本高。同时，大模型只支持阿里云 DashScope 一家，缺乏切换能力，未来接入 DeepSeek 等其他厂商时需要改动代码。

## What Changes

1. **知识库智能路由**：当用户选择"所有知识库"时，先用语义路由（Embedding 相似度）快速匹配最相关的 1-2 个知识库，低置信度时用 LLM 兜底。匹配不命中时降级为全量检索。
2. **LiteLLM Gateway 部署**：本地通过 Docker Compose 部署 LiteLLM Proxy（无 DB 模式），统一管理 LLM / Embedding 模型的路由和鉴权。应用代码通过 OpenAI 兼容接口调用 Proxy。
3. **模型配置重构**：LLM、Embedding、Rerank 三类模型独立配置（model / api_key / base_url），全部从 env 读取，切换 Provider 只改配置文件。
4. **Rerank 模型升级**：`gte-rerank-v1` 升级为 `qwen3-rerank`。

## Capabilities

### New Capabilities
- `kb-routing`: 用户选择"所有知识库"时，自动路由到最相关的 1-2 个知识库进行检索，提升检索精准度和效率
- `model-gateway`: LiteLLM Proxy 统一管理多 Provider 模型路由，应用通过 OpenAI 兼容接口调用
- `model-config`: LLM / Embedding / Rerank 三类模型各自独立配置，切换 Provider 只需改 env
- `rerank-upgrade`: Rerank 模型从 gte-rerank-v1 升级到 qwen3-rerank

### Modified Capabilities
- 无

## Impact

- **新增依赖**: LiteLLM Proxy Docker 镜像
- **新增文件**: `litellm/config.yaml`（Proxy 路由配置）
- **修改 docker-compose.yml**: 增加 litellm-proxy 服务
- **修改 src/config/settings.py**: 模型配置项重构
- **修改 src/models.py**: 模型工厂全面改为配置驱动
- **修改 src/rag/retrieval.py**: 全量检索改为路由检索
- **新增 src/rag/kb_router.py**: 知识库路由模块
- **修改 src/agents/graph/ 相关文件**: 插入路由节点
- **修改 src/config/queries.py 和 KbRepo**: 路由需要 KB description 字段
