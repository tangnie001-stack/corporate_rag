## Why

当前意图路由（QueryRouter）只按正则规则做三级分类（simple/vague/medium），LLM 兜底为 stub，无实体提取、无置信度评估、无追问能力。对话链路无法处理用户缺参（如"营收多少"缺年份）或表述模糊的场景。参考 financial_rag 的多层路由+追问模式，一次到位补齐意图理解全链路，提升首轮检索命中率。

## What Changes

- **QueryRouter 重写**：去掉语义路由层，改为"正则实体提取 → 复杂度评分 → LLM 合并输出"三层架构
- **实体提取模块新增**：正则提取财务关键实体（年份、季度、金额、指标等），0 LLM 成本
- **复杂度评分模块新增**：关键词加权评分（LOW/MEDIUM/HIGH/VERY_HIGH），作为 LLM 的 hint
- **classify_node 改造**：从纯函数改为工厂函数，一次 LLM 调用同时输出路由 + 补抽槽位 + 置信度 + 是否需要追问
- **追问能力新增**：当 classify 检测到缺关键实体时，SSE 流返回 clarification 事件，不走 rewrite/retrieve/generate
- **SSE 事件类型新增**：`SSEClarificationEvent`，前端需适配展示
- **清理重复分类器**：删除 `retrieval.py` 的 `classify_query()`，统一走 `query_router.py`
- **EMBEDDING_MODEL 默认值修复**：从 deepseek-embed-v4 改为 qwen3.7-text-embedding

## Capabilities

### New Capabilities
- `intent-classification`: 用户 query 的复杂度三级分类（simple/medium/complex），L0 问候拦截 + L1 正则 + L2 复杂度评分 + L3 LLM 兜底
- `entity-extraction`: 财务关键实体的正则+LLM 补抽取，含年份/季度/金额/指标/公司名
- `clarification-dialogue`: 缺参检测 + SSEClarificationEvent + 同 session 追问恢复流程

### Modified Capabilities
- *(无现有 spec 被修改)*

## Impact

- **API**：新增 `SSEClarificationEvent` 事件类型，现有 SSE 事件不变
- **Graph**：`workflow.py` 条件边新增 clarify → END 分支，`build_graph` 入参不变
- **配置**：`settings.py` 新增 `CLASSIFIER_TEMPERATURE`、`CLARIFICATION_ENABLED`
- **配置**：`EMBEDDING_MODEL` 默认值改为 `qwen3.7-text-embedding`
- **测试**：新增实体提取、复杂度评分、追问触发、澄清恢复等测试用例
- **前端**：需适配 `event: clarification` 事件，展示追问 UI + 快捷选项
