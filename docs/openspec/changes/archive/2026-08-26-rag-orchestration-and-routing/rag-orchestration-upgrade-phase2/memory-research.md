# AgentRAG 对话历史管理调研

## 一、业界最佳实践（LangGraph 官方方案）

LangGraph 官方推荐**三层内存架构**：

| 层级 | 组件 | 作用域 | 功能 | 存储 |
|:---|:---|:---|:---|:---|
| **L1 — 会话级** | Checkpointer + thread_id | 单次会话内 | 图执行状态自动快照，多轮对话上下文连贯 | MemorySaver / PostgresSaver |
| **L2 — 持久化记忆** | Store + Namespace | 跨会话，全局 | 用户偏好、事实性知识、事件记录 | InMemoryStore / PostgresStore / RedisStore |
| **L3 — 语义检索** | Store（配置 index） | 跨会话，全局 | Embedding 向量检索，语义匹配 | 在 Store 上配置 embed + dims |

**短期记忆管理策略：**
- **Trim（裁剪）**：超出 token 限制时从消息列表末尾丢弃
- **Delete（删除）**：从图状态中永久删除特定消息
- **Summarize（总结）**：用 LLM 对历史消息做摘要，用摘要替代原始消息（保留核心语义）

## 二、financial_rag 的做法

financial_rag 有完整的 `memory_system/` 模块：

```
app/memory_system/
├── working_memory.py          # 工作记忆（当前会话上下文）
├── episodic_memory.py         # 情景记忆（跨会话事件）
├── semantic_memory.py         # 语义记忆（知识图谱）
├── memory_cache.py             # 缓存层
├── storage_tier_scheduler.py  # 三层存储调度：HOT(Redis) → WARM(PostgreSQL) → COLD(Vector Store)
├── user_memory_extractor.py   # 用户信息提取
├── context_builder.py         # 上下文构建
└── memory_manager.py          # 统一管理器
```

比业界标准更复杂（多了分层存储调度），但核心思想和 LangGraph 官方推荐一致。

## 三、corporate_rag 当前做法

```
src/chat/
├── manager.py          # ChatManager：Redis 优先，InMemory 降级
│                       #   - 会话级：session_id → list[dict]
│                       #   - 滑动窗口：最近 N 条（默认 6）
│                       #   - Redis TTL：7 天
│                       #   - 自动降级：Redis 不可用 → 内存 dict
└── persistence.py      # PersistenceService：MySQL 持久化
```

两层：Redis（短期缓存）→ MySQL（长期持久化）

## 四、对比与差距

| 维度 | 业界标准 | financial_rag | corporate_rag（当前） | Phase 2 需要 |
|:---|:---|:---|:---|:---|
| 短期会话 | Checkpointer | working_memory + episodic | ✅ Redis + sliding window | ✅ 够用 |
| 跨会话记忆 | Store | semantic_memory | ❌ 无 | ❌ Phase 3 |
| 语义检索 | Store (index) | storage_tier_scheduler | ❌ 无 | ❌ Phase 3 |
| 自动摘要 | SummarizeNode | — | ❌ 无 | ❌ Phase 3 |
| 用户画像提取 | — | user_memory_extractor | ❌ 无 | ❌ Phase 3 |

## 五、结论：Phase 2 不需要升级记忆系统

**当前 ChatManager（Redis + 滑动窗口 + MySQL 持久化）已经满足 Phase 2 的需要。**

理由：
1. **Phase 2 不涉及跨会话记忆**——每次请求独立执行图，没有需要跨 session 持久化的 Agent 状态
2. **ChatManager 的滑动窗口（6 条）** 已经能提供基本的短期上下文
3. **MySQL 持久化** 保证对话记录不丢失
4. 升级到完整的 L2/L3 记忆系统会增加复杂度（需要引入 LangGraph Store、配置 Embedding 索引），而 Phase 2 的图编排和条件路由已经足够复杂

**建议**：Phase 2 保持现有 ChatManager，agent_service 通过 chat_manager 加载/保存历史。等 Phase 3 引入 Reflection 和 Human-in-loop 时，再根据实际需求评估是否需要 L2/L3 记忆。
