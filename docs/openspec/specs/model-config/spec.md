# model-config Specification

## Purpose
TBD - created by archiving change kb-routing-and-litellm-gateway. Update Purpose after archive.

## Requirements

### Requirement: 三类模型独立配置

系统 SHALL 支持 LLM、Embedding、Rerank 三类模型各自独立配置（model / api_key / base_url），全部从环境变量读取。

#### Scenario: 配置项独立
- **WHEN** 开发者在 `.env` 中设置 `LLM_MODEL`、`LLM_API_KEY`、`LLM_BASE_URL`
- **THEN** `get_llm()` 使用这些参数创建模型实例
- **WHEN** 开发者同时设置了 `EMBEDDING_MODEL`、`EMBEDDING_API_KEY`、`EMBEDDING_BASE_URL`
- **THEN** `get_embeddings()` 使用这些参数创建模型实例

#### Scenario: API Key fallback
- **WHEN** `LLM_API_KEY` 已设置，且 `EMBEDDING_API_KEY` 未设置
- **THEN** Embedding 使用 `LLM_API_KEY` 作为默认值
- **WHEN** `RERANK_API_KEY` 未设置
- **THEN** Rerank 使用 `DASHSCOPE_API_KEY` 作为默认值（而非 `LLM_API_KEY`，因为 Proxy 模式下 `LLM_API_KEY` 是 LiteLLM master key，DashScope Rerank 不识别）
- **WHEN** `DASHSCOPE_API_KEY` 也未设置
- **THEN** Rerank 回退到 `LLM_API_KEY`（纯 DashScope 场景下可行）
- **WHEN** 所有新 Key 均未设置，但 `DASHSCOPE_API_KEY` 存在
- **THEN** `LLM_API_KEY` fallback 到 `DASHSCOPE_API_KEY`
- **THEN** `EMBEDDING_API_KEY` fallback 到 `LLM_API_KEY`（即 `DASHSCOPE_API_KEY`）
- **THEN** `RERANK_API_KEY` fallback 到 `DASHSCOPE_API_KEY`
- **THEN** 系统正常启动，所有模型使用 DashScope
- **WHEN** 新旧配置同时存在
- **THEN** 新配置 (`LLM_API_KEY`) 优先级高于旧配置 (`DASHSCOPE_API_KEY`)

### Requirement: LLM 创建

`get_llm()` SHALL 使用 `ChatOpenAI`（LangChain）创建 LLM 实例，base_url 指向 LiteLLM Proxy。

#### Scenario: 正常创建
- **WHEN** 调用 `get_llm()`
- **THEN** 返回 `ChatOpenAI(model=LLM_MODEL, api_key=LLM_API_KEY, base_url=LLM_BASE_URL, temperature=LLM_TEMPERATURE)`

#### Scenario: Provider 特有参数
- **WHEN** 设置了 `LLM_KWARGS`（JSON 格式）
- **THEN** `get_llm()` 解包后传入 `ChatOpenAI` 的 `**kwargs`
- **THEN** 不同 Provider 的特有参数（如 thinking mode）通过此配置传递

### Requirement: Embedding 创建

`get_embeddings()` SHALL 使用 `OpenAIEmbeddings` 创建 Embedding 实例，通过 Proxy 或直连调用。

#### Scenario: 正常创建
- **WHEN** 调用 `get_embeddings()`
- **THEN** 返回 `OpenAIEmbeddings(model=EMBEDDING_MODEL, api_key=EMBEDDING_API_KEY, base_url=EMBEDDING_BASE_URL)`
- **THEN** 不传 `dimensions` 参数，依赖模型默认输出维度

#### Scenario: 向量维度风险
- **WHEN** 切换 Embedding Provider（如从 DashScope 改为 DeepSeek）
- **THEN** 需确认新模型输出维度与已有 ChromaDB collection 创建时的维度一致
- **THEN** 维度不匹配时查询会抛异常，需重建 collection 并 re-index

### Requirement: Rerank 创建

`get_rerank()` SHALL 继续使用 DashScope 的 Rerank API（DashScopeRerank）。

#### Scenario: 正常创建
- **WHEN** 调用 `get_rerank()`
- **THEN** 返回 `DashScopeRerank(model=RERANK_MODEL, ...)`

### Requirement: 配置默认值

所有配置项 SHALL 提供合理的默认值，保证 `.env` 缺失时系统可启动。

#### Scenario: DashScope 默认值
- **WHEN** `.env` 中只设置了 `DASHSCOPE_API_KEY`
- **THEN** `LLM_MODEL` 默认为 `qwen3.7-max`
- **THEN** `EMBEDDING_MODEL` 默认为 `qwen3.7-text-embedding`
- **THEN** `LLM_BASE_URL` 默认回退到 DashScope 直连地址 `https://dashscope.aliyuncs.com/compatible-mode/v1`
- **THEN** `EMBEDDING_BASE_URL` 默认同 `LLM_BASE_URL`
- **THEN** `LITELLM_MASTER_KEY` 有缺省值 `sk-test-123456`
- **THEN** 系统不依赖 LiteLLM Proxy 即可运行
