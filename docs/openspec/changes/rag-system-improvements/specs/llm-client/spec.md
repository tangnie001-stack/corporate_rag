## ADDED Requirements

### Requirement: LLM 实例化统一入口
所有 LLM 实例通过 `get_llm()` 创建，不使用 `ChatOpenAI(...)` 直接构造。

#### Scenario: eval_ragas.py 使用 get_llm
- **WHEN** 运行 RAGAS 评估（`cli/eval_ragas.py`）
- **THEN** LLM 通过 `get_llm(model=eval_model, temperature=0)` 创建，不是直接 `ChatOpenAI(...)`

#### Scenario: bypass_n 参数传递
- **WHEN** 评估或生成流程中创建 `LangchainLLMWrapper`
- **THEN** 传入 `bypass_n=True`，避免 DashScope API 不支持 n>1 参数

### Requirement: 调用日志记录
`get_llm()` 创建时记录模型名称和参数。

#### Scenario: 创建日志
- **WHEN** `get_llm()` 被调用
- **THEN** 日志记录 model、temperature 参数
