## Context

当前 LLM 调用追踪使用 `LangfuseTracer` 类手动打点（`start_trace/end_trace` + `start_generation/end_generation`），与业务代码（`rag/stream.py`、`rag/chain.py`、`api/chat.py`）高度耦合。还存在以下问题：

- `api/chat.py` 和 `rag/chain.py` 各自创建 trace，一次请求产生两条 trace 记录
- `LANGFUSE_ENABLE` 开关定义了但未被任何代码读取
- 日志记录与 tracing 分离，`rag/stream.py` 中既有 manual generation 打点又有 Loguru logger 调用
- 已有测试 `tests/rag/test_rag_chain_tracing.py` 按目标状态编写，测试期望 `_langfuse_handler` 和 `callbacks` 参数但实现未跟上

## Goals / Non-Goals

**Goals:**
- 用 Langfuse `@observe()` 装饰器替代手动 `start_trace/end_trace`，自动管理 trace 生命周期
- 用 `LangchainCallbackHandler`（Langfuse SDK 自带）替代手动 `start_generation/end_generation`，自动捕获 LLM 调用
- 新增自定义 `LoggingCallbackHandler`，将 LLM 调用信息写入 Loguru 日志（入参摘要、输出长度、token 用量、延迟）
- 两个 handler 通过 `llm.stream(callbacks=[...])` 参数注入，互不耦合
- `LANGFUSE_ENABLE` 开关真正生效，控制 Langfuse tracing 的启用/关闭
- 移除 `LangfuseTracer` 类，将所有 tracing 整合到新的 handler 模式
- 修复 `api/chat.py` 的双重 trace 问题

**Non-Goals:**
- 不改动现有 RAG 流水线的业务逻辑（检索、精排、prompt 构建）
- 不引入新的监控平台或依赖
- 不修改 CLI 模式下已有的 trace_id 日志注入逻辑

## Decisions

### 1. 全局 Langfuse 客户端单例

**方案：** 在 `langfuse_tracing.py` 中维护模块级 `_langfuse` 单例，供 `@observe()` 和 `CallbackHandler` 共享。

```python
_langfuse: Langfuse | None = None
_initialized = False

def _ensure_langfuse() -> Langfuse | None:
    """惰性初始化全局 Langfuse 客户端。"""
    global _initialized, _langfuse
    if _initialized:
        return _langfuse
    _initialized = True
    if not LANGFUSE_ENABLE:
        return None
    # ... 原有的 Langfuse 客户端初始化
```

**理由：** `@observe()` 装饰器和 `LangchainCallbackHandler` 需要共享同一个 Langfuse 客户端实例，否则无法自动关联 trace 上下文。模块级单例确保两者使用同一连接。

**替代方案考虑：** 之前尝试过通过 `RAGChain.__init__` 传递客户端实例，但 `@observe()` 不是由 RAGChain 直接调用的，无法传递。

### 2. 双 CallbackHandler 模式

**方案：** 同时注册两个 handler：

```python
callbacks = []
if langfuse_enabled:
    callbacks.append(LangchainCallbackHandler())  # 写 Langfuse
callbacks.append(LoggingCallbackHandler(...))      # 写日志，始终生效

llm.stream(messages, config={"callbacks": callbacks})
```

**理由：** LangChain callback 机制天然支持多个 handler 组合，各 handler 互不影响。`LangchainCallbackHandler` 来自 Langfuse SDK，负责 trace；`LoggingCallbackHandler` 是自定义的，负责日志。分离后任一方变化不影响另一方。

### 3. @observe 替代手动 trace

**方案：** 用 `@observe()` 装饰需要追踪的方法，Langfuse 自动捕获方法入参/返回/耗时：

```python
from langfuse.decorators import observe

@observe(name="chat_with_citations")
def chat_with_citations(self, kb_id, session_id, query):
    ...
```

`CallbackHandler` 会自动感知 `@observe()` 创建的 trace 上下文，将 generation 挂到正确的 trace 下，不需要手动传递 trace_id。

**理由：** `@observe()` 是 Langfuse SDK 提供的标准装饰器，与 `CallbackHandler` 天然集成。移除了 `api/chat.py` 中手动的 trace 创建后，整条链路统一由 `@observe()` 管理。

### 4. 双重 trace 修复

**方案：** 移除 `api/chat.py: _stream_rag_response()` 中手动创建的 `"chat_stream"` trace，让 `chain.py` 中的 `@observe()` 统一管理 trace。

### 5. LoggingCallbackHandler 设计

自定义 `BaseCallbackHandler`，覆盖以下事件：

| 事件 | 记录内容 |
|------|---------|
| `on_llm_start` | 模型名称、messages 数量+摘要、serialized 参数 |
| `on_chunk` | 收集 chunk（用于延迟计算） |
| `on_llm_end` | 完整输出长度、首 token 延迟、总耗时、LLMResult（含 token 用量） |
| `on_llm_error` | 异常信息、尝试次数 |

日志级别：正常完成用 INFO，首次调用用 INFO（含首 token 延迟），失败/异常用 WARNING/ERROR。

### 6. stream.py 核心逻辑简化

移除 `stream_answer()` 中的：
- `messages_snapshot` 构建（转为 `on_llm_start` 处理）
- `tracer.start_generation()` / `tracer.end_generation()`
- `estimate_usage()`（转为 `on_llm_end` 处理，从 `LLMResult` 中读取）
- 第一 token 延迟日志（转为 `on_chunk` / `on_llm_end` 处理）
- Generation completed 日志（转为 `on_llm_end` 处理）

重试逻辑不变，只是异常信息由 `on_llm_error` 记录。

## Risks / Trade-offs

- **CallbackHandler 版本兼容**：Langfuse SDK 的 `LangchainCallbackHandler` 依赖 `langchain-core` 版本。当前 `langchain-core` >= 1.0，Langfuse SDK 已支持。风险低。
- **LoggingCallbackHandler 与现有日志重复**：`stream_answer` 中现有日志可能在过渡期与 handler 日志重复。实现时应精确控制，handler 接管后移除 `stream_answer` 中的对应日志行。
- **`@observe()` 不支持同步/异步混合**：`chat_with_citations` 是同步方法，`_stream_rag_response` 是异步生成器。如果需在异步端使用 `@observe`，需额外处理。目前 `@observe` 只加在同步方法 `chat_with_citations` 上。
- **回滚**：如果新 tracing 出现问题，可以将 `LANGFUSE_ENABLE` 设为 `false` 关闭 Langfuse 追踪，日志 handler 不受影响。如需完全回退，旧代码通过 git revert 恢复。

## Migration Plan

1. 实现 `LoggingCallbackHandler`（新文件）
2. 重构 `langfuse_tracing.py`：全局单例 + 工厂函数，保留 `LangfuseTracer` 作短暂过渡
3. 修改 `rag/chain.py`：加 `@observe`，用 `create_langfuse_handler()` 替代 `LangfuseTracer`
4. 修改 `rag/stream.py`：移除手动打点，改用 `callbacks` 参数
5. 修改 `api/chat.py`：移除手动 trace 创建
6. 更新测试：`test_langfuse.py`、`test_rag_chain_tracing.py`、`test_chat.py`
7. 验证：`pytest tests/ -v` + 手动触发 /chat/stream 端点，检查 trace 和日志

## Open Questions

- `_stream_rag_response()` 是异步生成器，`@observe()` 在异步生成器上的行为需要验证——是否需要先不装饰异步端，只从 `chain.chat_with_citations()` 同步入口走？
