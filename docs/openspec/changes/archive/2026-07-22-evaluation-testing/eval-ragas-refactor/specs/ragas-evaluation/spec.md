## ADDED Requirements

### Requirement: 指定知识库运行评估
系统 SHALL 通过 `--kb-name` 参数指定要评估的知识库名称。该参数为必填，不传时 argparse 自动报错。

#### Scenario: 不传 kb-name 报错
- **WHEN** 执行 `python -m src.cli.eval_ragas` 不带 `--kb-name` 参数
- **THEN** argparse 输出错误信息并退出

#### Scenario: 指定已有知识库
- **WHEN** 执行 `python -m src.cli.eval_ragas --kb-name "我的知识库"`
- **THEN** 系统对该知识库运行完整 RAGAS 评估

### Requirement: 列出可用知识库
系统 SHALL 通过 `--list-kbs` 参数列出 MySQL 中所有知识库名称和文档数，列完后直接退出不执行评估。

#### Scenario: 列出知识库
- **WHEN** 执行 `python -m src.cli.eval_ragas --list-kbs`
- **THEN** 打印知识库名称和文档数列表，然后退出

### Requirement: 评估 LLM 独立配置
系统 SHALL 支持通过 `RAGAS_LLM_MODEL` 环境变量指定评估专用 LLM 模型。该模型独立于生产 LLM，temperature 固定为 0。

配置为空时回退到 `LLM_MODEL`。

#### Scenario: 使用独立评估模型
- **WHEN** 设置了 `RAGAS_LLM_MODEL=deepseek-chat`
- **THEN** 评估使用 deepseek-chat 模型且 temperature=0

#### Scenario: 回退到生产模型
- **WHEN** 未设置 `RAGAS_LLM_MODEL`
- **THEN** 评估使用 `LLM_MODEL` 且 temperature=0

## REMOVED Requirements

### Requirement: Chunk size comparison
**Reason**: 项目已全面转向父子分块，分块策略由 `ChunkRouter` 自动路由，单 `--chunk-size` 参数已无法反映实际分块行为。`compare_chunk.py` 已被删除。
**Migration**: 分块参数实验请使用实际入库后的 RAGAS 评估结果对比。

## MODIFIED Requirements

### Requirement: QA test pair coverage

The system SHALL maintain a minimum of **50** QA test pairs covering at least two distinct financial documents (e.g., 贵州茅台 2024 年报 and 厦门灿坤 2019 年报).

Test questions SHALL cover the following dimensions:
- Revenue and growth metrics
- Per-share earnings and shareholder structure
- Core business analysis
- Regional/segment performance
- Company basic information

#### Scenario: QA pair count meets minimum
- **WHEN** running `python -m src.cli.eval_ragas --check`
- **THEN** the system SHALL report the total QA pair count and exit with 0

#### Scenario: QA pair count below minimum
- **WHEN** QA pair count is below 50
- **THEN** the system SHALL print guidance message and exit with 1 when in standalone `--check` mode, or print a warning and continue evaluation when in companion mode
