# chat-temperature-policy Proposal

## Why

主 agent 采样温度当前全局固定 0.1（LLM_TEMPERATURE）。绑 KB（RAG）场景低温利于事实与引用一致，合理保留；但非 KB 的聊天/探讨/观点类回答在 0.1 下偏干、机械，用户体感不佳。需要按会话是否绑定 KB 分档：绑 KB 0.1；非 KB 0.6（默认，参数化）。

## What Changes

- 主 agent 温度由全局常量改为按请求选择：`temperature = 0.1 if kb_id 非空 else 0.6`（0.6 进 settings 可调）。
- 实现路径：agent 节点在每轮调用处直传 `temperature` kwarg（与 enable_thinking 同处；仓库已有 `ainvoke(..., temperature=0)` 先例）。同请求档位恒定。
- 分档只作用于主 agent 采样；fork 子代理、分类/检索等内部调用不受影响（子代理采样由 skill 层另行定义，本 change 不引入 skill temperature 字段）。
- 档位判定源为 `state.kb_id`（请求首轮即固定），同请求全轮一致、中途不随多轮/委派变化。
- 非 KB 会话可能含 search_web 联网事实答案：0.6 会同样作用于此类事实题；如联网事实题占比较高，可经参数下调（如 0.4-0.5），0.6 仅为本 change 定稿默认。

## Capabilities

### New Capabilities
- `chat-temperature-policy`: 主 agent 采样温度按会话是否绑定 KB 分档（0.1 / 0.6）

### Modified Capabilities
- （无）

## 与其它 change 的关系
- 独立 change，无共享文件冲突：仅触碰 `agent_node.py`/`settings.py` 及其测试；**可与 core 并行实施**（core 不涉及 agent_node），也可随时插入。
- 实施顺序约束（统一）：`core → 温度(可并行) → task-board`；本 change 不依赖其它 change。

## Impact

- `src/agents/graph/agent_node.py` — 调用处按 `state.kb_id` 直传 temperature
- `src/config/settings.py` — 非 KB 默认温度参数
- 测试：agent_node 流式路径 payload 断言 + kb 有无命中档位
- 归属文档：api_contract.md（如描述温度）
