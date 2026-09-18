## MODIFIED Requirements

### Requirement: Embedding 创建

`get_embeddings()` SHALL 使用 `OpenAIEmbeddings` 创建 Embedding 实例，通过 Proxy 或直连调用。

#### Scenario: 正常创建
- **WHEN** 调用 `get_embeddings()`
- **THEN** 返回 `OpenAIEmbeddings(model=EMBEDDING_MODEL, api_key=EMBEDDING_API_KEY, base_url=EMBEDDING_BASE_URL)`
- **THEN** 不传 `dimensions` 参数，依赖模型默认输出维度

#### Scenario: 向量维度风险
- **WHEN** 切换 Embedding Provider（如从 DashScope 改为 DeepSeek）
- **THEN** 需确认新模型输出维度与已有 `chunks.embedding` 列的声明维度一致
- **THEN** 维度不匹配时写入或查询会失败，需变更列定义并重建全部分块向量
