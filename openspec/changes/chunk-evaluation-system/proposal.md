## Why

当前分块策略调整后缺乏自动化的质量反馈机制。每次改完参数需要手动查看分块结果、凭经验判断好坏，效率低且标准不统一。需要一套自动化的前置评估体系，让每次分块后都能知道"这次分得怎么样"。

## What Changes

- **新增 `chunk-quality-scorer` 能力**：上传文件后自动跑 3 个轻量指标（结构完整性、SBR 语义断裂率、粒度 CV），结果写入 `document.meta_info` JSON 列
- **新增全局开关 `CHUNK_EVAL_ENABLED`**：控制评估是否启用，默认关闭，不影响现有流程
- **新增 `eval-report-storage` 能力**：新建 `eval_report` 表存储 RAGAS 评估结果，KB 级展示
- **修改 `evaluation-pipeline` 规范**：现有 RAGAS CLI 评估 (`python -m src.cli.eval_ragas`) 新增写入 `eval_report` 表
- **前端 API 扩展**：`DocumentListResponse` 新增 `eval_score`、`eval_passed`、`eval_detail` 字段
- 评估模式为 C（只记录不拦截），低分文件前端标红，不影响入库和检索

## Capabilities

### New Capabilities
- `chunk-quality-scorer`: 上传后自动计算 3 个轻量指标（结构完整性/SBR/粒度CV），结果写入 document.meta_info
- `eval-report-storage`: RAGAS 评估结果持久化到 eval_report 表，前端 KB 头部展示最新评分

### Modified Capabilities
- `evaluation-pipeline`: 现有的 RAGAS CLI 评估 (eval_ragas.py) 新增写入 eval_report 表的功能

## Impact

- `src/config/settings.py` — 新增 `CHUNK_EVAL_ENABLED` 开关
- `src/api/model/response.py` — `DocumentListResponse` 加 3 个字段
- `src/api/documents.py` — `_process_document_task` 插入评估调用
- **新增** `src/eval/chunk_scorer.py` — 3 指标计算 + 综合分 + heading 正则检测
- `src/cli/eval_ragas.py` — 评估完写入 `eval_report` 表
- **新增** `src/infra/db/queries.py` — `eval_report` DDL + CRUD
- 前端 — 列表行展示 + 一级/二级弹窗交互
