## 1. 基础设施与配置

- [ ] 1.1 `settings.py`：EMBEDDING_MODEL 默认值改为 qwen3.7-text-embedding
- [ ] 1.2 `settings.py`：新增 CLASSIFIER_TEMPERATURE、CLARIFICATION_ENABLED 配置项
- [ ] 1.3 `prompts.py`：新增 CLASSIFIER_SYSTEM_PROMPT 和 CLASSIFIER_USER_TEMPLATE 常量
- [ ] 1.4 `prompt_manager.py`：新增 get_classifier_prompt() 方法，支持 Langfuse 拉取 + 本地兜底

## 2. 实体提取模块

- [ ] 2.1 新建 `src/infra/search/entity_extractor.py`：实现 EntityExtractor 类，正则模式覆盖 year/quarter/month/metric/money/percentage/company
- [ ] 2.2 实现 ExtractedEntity dataclass（type, value, confidence, source）
- [ ] 2.3 新建 `tests/infra/search/test_entity_extractor.py`：测试各实体类型的正则匹配

## 3. 复杂度评分模块

- [ ] 3.1 新建 `src/infra/search/complexity_scorer.py`：实现复杂度加权评分（LOW/MEDIUM/HIGH/VERY_HIGH）
- [ ] 3.2 实体数量加分 + "和/或/与" 加分逻辑
- [ ] 3.3 新建 `tests/infra/search/test_complexity_scorer.py`：测试各复杂度级别的评分结果

## 4. QueryRouter 重写

- [ ] 4.1 重写 `src/infra/search/query_router.py`：封装 EntityExtractor、ComplexityScorer、LLM 三层；接受 llm 参数
- [ ] 4.2 实现 L0 问候/长度拦截（直接返回 simple，不调用 LLM）
- [ ] 4.3 实现 L1 → L2 → L3 串联逻辑（实体提取 → 复杂度评分 → LLM）
- [ ] 4.4 QueryRouter 接受 llm 参数，调用 LLM 做分类+补抽槽位
- [ ] 4.5 更新 `tests/infra/search/test_query_router.py`：覆盖新三层路由测试

## 5. AgentState 扩展

- [ ] 5.1 `state.py`：AgentState 新增 extracted_entities、missing_entities、classification_confidence 字段
- [ ] 5.2 更新 RAGQueryIntent（不变或确认无改动）
- [ ] 5.3 更新 test_state.py 验证新增字段默认值

## 6. classify_node 改造

- [ ] 6.1 `nodes.py`：classify_node 从纯函数改为 `make_classify_node(llm)` 工厂函数
- [ ] 6.2 classify_node 内部实例化 QueryRouter(llm)，委托调用三层路由
- [ ] 6.3 classify_node 调用 LLM 获取 route + missing_entities + confidence
- [ ] 6.4 classify_node 返回完整 dict（含 extracted_entities、missing_entities、classification_confidence）
- [ ] 6.5 修改 `route_by_intent` 现有函数，加 `"clarify": END` 分支（不新增函数）
- [ ] 6.6 `workflow.py`：builder.add_node 使用 make_classify_node(llm)
- [ ] 6.7 `workflow.py`：条件边新增 "clarify": END 分支

## 7. SSE 新事件

- [ ] 7.1 `sse.py`：新增 SSEClarificationEvent dataclass
- [ ] 7.2 实现 sse_clarification() 格式化函数
- [ ] 7.3 to_sse() 新增 SSEClarificationEvent 分发分支

## 8. agent_service 改造

- [ ] 8.1 `agent_service.py`：stream_chat 在 CHAIN_END 中捕获 classify 输出
- [ ] 8.2 当检测到 missing_entities 时存储 clarification 信息
- [ ] 8.3 循环结束后，如有 clarification 则发送 SSEClarificationEvent + done 并提前 return
- [ ] 8.4 正常路径（无 clarification）行为不变

## 9. 清理重复分类器

- [ ] 9.1 `retrieval.py`：删除 classify_query() 函数
- [ ] 9.2 `retrieval.py`：rewrite_query 改为接收 intent_route 参数，不再内部分类
- [ ] 9.3 更新 rewrite_query 的所有调用方（nodes.py 中 rewrite_node）
- [ ] 9.4 更新 tests 中 mock classify_query 的部分

## 10. 追问流程测试

- [ ] 10.1 测试 missing_entities 触发条件（缺年份/缺指标/无 history）
- [ ] 10.2 测试 history 补齐后不触发追问（上轮有年份信息）
- [ ] 10.3 测试 SSE clarification 事件格式正确
- [ ] 10.4 测试同 session 追问→回答→正常检索的完整链路（tests/services/test_agent_service.py）
- [ ] 10.5 测试 graph 条件边 "clarify" → END 路径

## 11. 集成验证

- [ ] 11.1 `pytest tests/ -v` 全部通过
- [ ] 11.2 `ruff check .` 无错误
- [ ] 11.3 无遗留 print()、TODO 或调试代码
- [ ] 11.4 代码位置检查：新增/修改的代码在正确的目录
- [ ] 11.5 层次检查：api/ 里只做路由转发，没有写业务逻辑
- [ ] 11.6 import 检查：没有违反层间调用规则
