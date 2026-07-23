## ADDED Requirements

### Requirement: CLI 支持 --generate 模式
系统 SHALL 在 `eval_ragas.py` 中提供 `--generate` CLI 参数，进入测试集生成模式。支持同时指定 `--size N` 和 `--model MODEL` 覆盖默认配置。

#### Scenario: 使用 --generate 生成测试集
- **WHEN** 用户运行 `python -m src.cli.eval_ragas --kb-name "我的知识库" --generate --size 30`
- **THEN** 系统从 MinIO 下载该知识库所有文档 → 解析 → 传给 ragas TestsetGenerator → 生成 30 条 QA 对
- **THEN** 测试集保存到 `data/ragas/testset_{kb_id}_v1.json`（首次）

#### Scenario: 默认 size 使用配置值
- **WHEN** 用户运行 `python -m src.cli.eval_ragas --kb-name "我的知识库" --generate` 未指定 `--size`
- **THEN** 使用 `settings.RAGAS_TEST_SIZE` 的值（默认 20）

### Requirement: 从 MinIO 取文档
系统 SHALL 在生成测试集时，从 MySQL 查询知识库的文档列表 → 从 MinIO 下载原始文件 → 使用 parser 解析 → 拼完整文本。

#### Scenario: 成功生成测试集
- **WHEN** 知识库中有 3 份已入库文档
- **THEN** 系统下载 3 份原始文件 → 解析 → 传给 TestsetGenerator
- **THEN** 生成的 metadata.doc_ids 包含 3 个文档 UUID

#### Scenario: 知识库不存在
- **WHEN** 用户指定不存在的 kb-name
- **THEN** 系统打印错误并退出码为 1

### Requirement: 测试集带 metadata
系统 SHALL 生成的测试集以 JSON 格式保存，包含 metadata（kb_name、version、generated_at、llm_model、testset_size、ragas_version、doc_ids）和 samples 数组。

#### Scenario: 重新生成递增版本号
- **WHEN** 用户对一个知识库第二次运行 `--generate`
- **THEN** 系统检测到 `testset_{kb_id}_v1.json` 已存在
- **THEN** 新文件保存为 `testset_{kb_id}_v2.json`，metadata.version 为 2

#### Scenario: 原子写入防止半成品
- **WHEN** 系统写入 JSON 测试集文件
- **THEN** 先写到 `{filename}.tmp` 临时文件
- **THEN** 使用 `os.replace()` 原子替换为目标文件
- **THEN** 生成过程中断电或异常不会留下损坏的目标文件

### Requirement: 评估自动使用最新测试集
系统 SHALL 在无 `--generate` flag 时，自动按 kb_name 查找 `data/ragas/` 下最新版本的测试集 JSON 加载为 QA 对进行评估。

#### Scenario: 评估使用最新版本测试集
- **WHEN** `data/ragas/` 下有 `testset_{kb_id}_v1.json` 和 `testset_{kb_id}_v2.json`
- **THEN** 系统提取版本号取最大（v2），加载该文件用于评估

#### Scenario: 无测试集时退出
- **WHEN** 运行评估但 `data/ragas/` 下没有该知识库的测试集文件
- **THEN** 系统打印 "请先运行 --generate 生成测试集" 并退出码为 1

### Requirement: 删除手工 QA 对
系统 SHALL 删除 `src/config/ragas_pairs.py` ，评估不再依赖手工 QA 对。

#### Scenario: ragas_pairs.py 不再存在
- **WHEN** 评估流程运行
- **THEN** 不再 import `src.config.ragas_pairs`
- **THEN** 评估数据源完全来自 `data/ragas/` 下的测试集 JSON

> **测试范围说明**：`eval_ragas_generate.py` 依赖 MinIO、Parser、ragas TestsetGenerator 等外部组件，自动化测试成本高。本次不新增自动化测试，验证通过手动命令执行（见 tasks 第 5 章）。

### Requirement: 生成过程中的错误处理
系统 SHALL 在 `--generate` 模式下区分可恢复错误和致命错误，分别处理。

#### Scenario: 单份文档解析失败
- **WHEN** 知识库有 3 份文档，第 2 份解析抛出异常
- **THEN** 系统 `logger.warning` 记录失败详情
- **THEN** 系统 `print` 输出 `⚠ 文档 xxx.pdf 解析失败，已跳过`
- **THEN** 跳过该文档，继续处理第 3 份

#### Scenario: 基础设施故障
- **WHEN** MinIO 连接超时或数据库查询失败
- **THEN** 系统 `logger.exception` 记录完整 traceback
- **THEN** 系统 `print` 输出 `✗ 测试集生成失败: <错误描述>`
- **THEN** 系统 `sys.exit(1)` 退出

#### Scenario: TestsetGenerator 调用异常
- **WHEN** ragas TestsetGenerator 内部抛出异常（如 LLM 调用超时）
- **THEN** 系统 `logger.exception` 记录完整 traceback
- **THEN** 系统 `print` 输出 `✗ 测试集生成失败: 请检查 LLM 配置和网络连接`
- **THEN** 系统 `sys.exit(1)` 退出

#### Scenario: 生成过程中 Ctrl+C
- **WHEN** 用户在生成过程中按 Ctrl+C
- **THEN** 系统自然终止，不写入半成品 JSON 文件
- **THEN** 重跑时版本号正常递增
