# model-gateway Specification

## Purpose

定义**可选**的 LiteLLM 模型网关：它**不参与默认运行路径**（compose profile `disabled`），仅在需要统一路由 / 跨 Provider fallback 时手动启用。默认形态是三类模型各自直连 Provider —— 该决策见 `docs/adr/0020-no-model-gateway-direct-provider.md`。

## Requirements

### Requirement: 可选模型网关（compose profile `disabled`）

LiteLLM Proxy SHALL 以 compose profile `disabled` 提供，**默认不随 `docker compose up -d` 启动**（`docker-compose.yml` 的 `litellm-proxy`）。

#### Scenario: 默认不启动
- **WHEN** 未指定 profile 执行 `docker compose up -d`
- **THEN** 不启动 `litellm-proxy` 容器
- **THEN** 应用仍能完成问答（三类模型默认直连 Provider）

#### Scenario: 显式启用
- **WHEN** 执行 `docker compose up -d litellm-proxy`
- **THEN** Proxy 容器启动，监听 4000 端口
- **THEN** 显式指定服务名可越过 profile 门（`profiles` 只影响"不带服务名的 `up`"）

#### Scenario: 资源与运行形态
- **WHEN** 启用 litellm-proxy
- **THEN** 容器的 `mem_limit` 为 768m、`mem_reservation` 为 512m（该进程内存需求较高，512m 曾实测不可靠）
- **THEN** 使用无 DB 模式（PostgreSQL 不需要）
- **THEN** 单 Worker 运行（`--num_workers 1`）
- **THEN** 日志级别 WARNING，减少输出

### Requirement: 多 Provider 路由（仅 LLM）

启用时，LiteLLM Proxy SHALL 通过 `litellm/config.yaml` 配置 LLM 的 Provider 路由与 fallback。

#### Scenario: DashScope LLM
- **WHEN** `config.yaml` 配置了 DashScope 的 LLM 条目
- **THEN** 应用以该条目的 `model_name` 调用，无需关心后端 API 地址与 Key

#### Scenario: 跨 Provider fallback
- **WHEN** `config.yaml` 配置了 `fallbacks`（如 DashScope 主模型 → DeepSeek 备模型）
- **THEN** 主模型失败时由 Proxy 自动切到备模型
- **THEN** 备模型所在 Provider 的 Key 必须已配（否则该 fallback 会认证失败）

#### Scenario: Embedding 与 Rerank 不经网关
- **WHEN** 需要 Embedding 或 Rerank
- **THEN** 二者**不经** LiteLLM Proxy —— Rerank 的原生实现在 `src/models.py` 的 `get_rerank()`，Provider 不支持经该网关调用其 rerank 端点

### Requirement: 请求鉴权

启用时，LiteLLM Proxy SHALL 使用 `LITELLM_MASTER_KEY` 做请求鉴权，应用代码在调用时携带此 Key。

#### Scenario: 鉴权通过
- **WHEN** 应用调用 Proxy API 时携带正确的 `Authorization: Bearer LITELLM_MASTER_KEY`
- **THEN** Proxy 正常转发请求

#### Scenario: 鉴权失败
- **WHEN** 应用调用 Proxy API 时未携带或不正确的 Key
- **THEN** Proxy 返回 401 Unauthorized
