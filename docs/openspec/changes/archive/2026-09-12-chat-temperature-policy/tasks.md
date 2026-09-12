## 1. 配置与实现

- [ ] 1.1 `settings.py` 增加非 KB 主 agent 默认温度参数（默认 0.6，绑 KB 固定 0.1 常量）
- [ ] 1.2 `agent_node.py`：每轮 `model.astream(...)` 调用处按 `state.kb_id` 直传 `temperature=`（kb 空→0.6，否则 0.1），与 enable_thinking 同处
- [ ] 1.3 首步流式冒烟/单测：拦截 astream 调用断言 payload 温度=档位值；kb 有无分别命中；同请求多轮一致
- [ ] 1.4 非 KB 默认值可配置性验证

## 2. 文档与收尾

- [ ] 2.1 api_contract.md/data-flow.md 同步（温度档位说明，如涉及）
- [ ] 2.2 openspec validate 通过；质量门禁（pytest/ruff/pyright）全绿
