## ADDED Requirements

### Requirement: LiteLLM Proxy Docker 部署
系统 SHALL 通过 Docker Compose 部署 LiteLLM Proxy，作为统一的模型网关。

#### Scenario: 本地启动
- **WHEN** 执行 `docker compose up -d litellm-proxy`
- **THEN** Proxy 容器启动，监听 4000 端口
- **THEN** 应用代码可通过 `http://litellm-proxy:4000` 调用 LLM 和 Embedding API

#### Scenario: 内存限制
- **WHEN** 本地开发环境启动 litellm-proxy
- **THEN** 容器的 mem_limit 为 256m，mem_reservation 为 128m
- **THEN** 使用无 DB 模式（SQLite 不必须，PostgreSQL 不需要）
- **THEN** 单 Worker 运行（`--num_workers 1`）
- **THEN** 日志级别 WARNING，减少输出

### Requirement: 多 Provider 路由
LiteLLM Proxy SHALL 通过 `config.yaml` 配置 DashScope 和 DeepSeek 的模型路由。

#### Scenario: 配置 DashScope 模型
- **WHEN** config.yaml 配置了 DashScope 的 LLM 和 Embedding 模型
- **THEN** 应用通过 `model_name` 即可调用，无需关心后端 API 地址和 Key

#### Scenario: 配置 DeepSeek 模型
- **WHEN** config.yaml 配置了 DeepSeek 的 LLM 和 Embedding 模型
- **THEN** 应用通过 `model_name` 即可调用，无需关心后端 API 地址和 Key

#### Scenario: 切换模型
- **WHEN** 开发者修改 config.yaml 中的模型路由
- **THEN** 执行 `docker compose restart litellm-proxy` 生效
- **THEN** 应用代码无需任何改动

### Requirement: 请求鉴权
LiteLLM Proxy SHALL 使用 `LITELLM_MASTER_KEY` 做请求鉴权，应用代码在调用时携带此 Key。

#### Scenario: 鉴权通过
- **WHEN** 应用调用 Proxy API 时携带正确的 `Authorization: Bearer LITELLM_MASTER_KEY`
- **THEN** Proxy 正常转发请求

#### Scenario: 鉴权失败
- **WHEN** 应用调用 Proxy API 时未携带或不正确的 Key
- **THEN** Proxy 返回 401 Unauthorized
