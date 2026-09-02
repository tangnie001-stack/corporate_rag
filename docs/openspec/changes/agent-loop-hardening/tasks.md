# agent-loop-hardening Tasks

## 1. C1-5 verify 包化（地基，纯重构先行，独立 commit）

- [ ] 1.1 `verify_node.py` → `src/agents/graph/verify/` 包：拆分 node/checks/guardrails/ask_confirm/faithfulness/pipeline
- [ ] 1.2 按两态分派管道：态 A `[web_citation_guard]` / 态 B `[completeness, kb_guardrail, faithfulness]`；workflow.py 导入名 `verify_node` 不变
- [ ] 1.3 校验器管道执行器 pipeline.py：遍历 CHECKS 短路返回，校验器返回 None=通过 / dict=决策
- [ ] 1.4 纯重构回归：全量 pytest 通过，行为零变化（此时不加新功能，只搬代码）
- [ ] 1.5 **测试 import 兼容**：`verify/__init__.py` re-export `verify_node`/`faithfulness_check`/`_ask_web_confirm` 等，`tests/agents/graph/test_verify_node.py` 顶层 import 与 monkeypatch 路径不变（22 个用例零改或最小改）
- [ ] 1.6 `verify/` 包单测（管道短路、两态分派）

## 2. C1-1 kb_router 下移（动图删节点）

- [ ] 2.1 `workflow.py` 删 kb_router 节点，entry_point 直连 "agent"；nodes.py 删 `make_kb_router_node`
- [ ] 2.2 `state.py` 删 `_resolved_kb_ids` 字段 + `LangGraphNode.KbRouter` 类
- [ ] 2.3 消费点换源：rag_tools.retrieve_kb → `[state.kb_id]`；verify 态判定 → `not state.kb_id`；ask_tools `_load_dimension_options` → `state.kb_id`
- [ ] 2.4 **清死代码**：删 `src/rag/kb_router.py`（KBRouter 类，P0 删 `_semantic_select_kb` 后已无调用者）；`workflow.build_graph` / `make_rag_tools` 死参数（embed_fn/classify_llm，仅 kb_router 用）；同步 models.py/settings.py 中 KBRouter 相关注释
- [ ] 2.5 `test_graph.py` `test_graph_topology` 更新（节点集合去 kb_router）；test_rag_tools / test_verify_node / test_ask_user 相关断言更新
- [ ] 2.6 回归：绑定 KB 检索正常、未绑定不检索（kb 两态语义不变）

## 3. C1-2 verify regen 决策化（bug 修复 + 根本重构）

- [ ] 3.1 `state.py` 加 `_verify_regenerations: int = 0`；`const.py` 加 `MAX_VERIFY_REGENERATIONS = 2`（保险丝）
- [ ] 3.2 verify 态B missing 分支决策化：读 messages 最近 search_web tool_call 的 args.queries → 没调过→注入指引 regen；queries 未带全 missing→regen 强调一次带全；带全仍缺→标注直通
- [ ] 3.3 态 A web 引用引导分支同步：`_agent_iterations < max` 检查换源，引导一次后直通
- [ ] 3.4 agent_node/route_agent 保持 `_agent_iterations` 只管主循环，不再被 verify 复用
- [ ] 3.5 新增图集成测试：①首轮耗 4 次迭代后 verify regen search_web 完整执行（回归 V2 bug）；②联网带全仍缺 → 标注直通不空转；③queries 未带全 → 再试一次
- [ ] 3.6 回归：既有 verify 循环测试（test_graph_verify_loop_*）适配决策化

## 4. C1-3 输出护栏（KB 强制溯源）

- [ ] 4.1 `guardrails.py` 实现 kb_citation_guardrail：触发条件 = has_kb_context ∧ 无[n] ∧ 非拒答 ∧ 非"知识库未覆盖"
- [ ] 4.2 `const.py` 加 KB citation marker（区分 web 版）；防重复注入查重复用
- [ ] 4.3 加入态 B 管道（completeness 通过后、faithfulness 前）
- [ ] 4.4 单测：触发引导 / 拒答不触发 / "知识库未覆盖"不触发 / 防重复注入
- [ ] 4.5 回归：前端 KB 答案带 [n] 后引用横条正常

## 5. 质量门禁

- [ ] 5.1 `pytest tests/ -v` 全量通过；`ruff check .` 无错误；`pyright src/` 不新增 error
- [ ] 5.2 契约同步：api_contract.md（如有公共方法签名变化）、glossary.md（如有新术语）、rules.md 无冲突
- [ ] 5.3 手动验证：绑定 KB 无 [n] 答案被引导补标；未绑定 KB 纯对话不跑 judge；regen 后 search_web 完整执行不空白
