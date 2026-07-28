# KB Routing + LiteLLM Gateway 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 引入 LiteLLM Proxy 统一管理多 Provider 模型路由，实现知识库智能路由（语义路由 + LLM 兜底）

**Architecture:** Docker Compose 中新增 LiteLLM Proxy 容器（无 DB 模式），应用代码通过 OpenAI 兼容接口调用 Proxy。LLM / Embedding / Rerank 三类模型各自独立配置（model / api_key / base_url），全部从 env 读取。当用户选择"所有知识库"时，通过 KBRouter 实时路由到最相关的 1-2 个 KB。

**Tech Stack:** LiteLLM Proxy, LangChain (ChatOpenAI, OpenAIEmbeddings, DashScopeRerank), LangGraph

## Global Constraints

- LiteLLM Proxy 使用无 DB 模式（无需 PostgreSQL）
- 应用代码使用 `ChatOpenAI` 和 `OpenAIEmbeddings`，不引入 `litellm` Python SDK
- `RERANK_API_KEY` fallback 到 `DASHSCOPE_API_KEY`（而非 `LLM_API_KEY`）
- `LLM_BASE_URL` 默认指向 `http://litellm-proxy:4000`
- config.yaml 前缀规则：DashScope → `openai/`，DeepSeek → `deepseek/`
- 嵌入向量模型切换需明确确认且需 re-index

---

## 文件结构

```
litellm/config.yaml                          ← 新建：LiteLLM Proxy 路由配置
docker-compose.yml                           ← 修改：添加 litellm-proxy 服务
.env                                         ← 修改：添加新配置项
.env.example                                 ← 修改：更新示例配置

src/config/settings.py                       ← 修改：重构模型配置项
src/models.py                                ← 修改：配置驱动 Factory
src/infra/db/entities/kb.py                  ← 修改：KbListItem 加 description
src/config/queries.py                        ← 修改：SQL 加 description
src/infra/db/mysql_db/kb_repo.py             ← 修改：get_all_kb 返回 description
src/rag/kb_router.py                         ← 新建：KBRouter 语义路由 + LLM 兜底
src/agents/graph/state.py                    ← 修改：加 _resolved_kb_ids
src/agents/graph/nodes.py                    ← 修改：加 kb_router_node
src/agents/graph/workflow.py                 ← 修改：注册 kb_router_node
pyproject.toml                                ← 修改：加 numpy 依赖
```

---

## Task 1: LiteLLM Proxy 部署

**Files:**
- Create: `litellm/config.yaml`
- Modify: `docker-compose.yml`
- Modify: `.env`

**Interfaces:**
- Produces: LiteLLM Proxy 服务在 `http://litellm-proxy:4000` 运行，接受 `Authorization: Bearer <LITELLM_MASTER_KEY>` 请求

- [ ] **1.1 创建 `litellm/config.yaml`**

```yaml
# litellm/config.yaml
model_list:
  # DashScope LLM（OpenAI 兼容模式）
  - model_name: qwen3.7-max
    litellm_params:
      model: openai/qwen3.7-max
      api_key: os.environ/DASHSCOPE_API_KEY
      api_base: https://dashscope.aliyuncs.com/compatible-mode/v1

  # DeepSeek LLM（原生模式）
  - model_name: deepseek-v4-pro
    litellm_params:
      model: deepseek/deepseek-v4-pro
      api_key: os.environ/DEEPSEEK_API_KEY
      api_base: https://api.deepseek.com

  # DashScope Embedding（OpenAI 兼容模式）
  - model_name: qwen3.7-text-embedding
    litellm_params:
      model: openai/qwen3.7-text-embedding
      api_key: os.environ/DASHSCOPE_API_KEY
      api_base: https://dashscope.aliyuncs.com/compatible-mode/v1

  # DeepSeek Embedding（OpenAI 兼容模式）
  - model_name: deepseek-embed-v4
    litellm_params:
      model: openai/deepseek-embed-v4
      api_key: os.environ/DEEPSEEK_API_KEY
      api_base: https://api.deepseek.com
```

- [ ] **1.2 在 `docker-compose.yml` 中添加 litellm-proxy 服务**

```yaml
  # ── 在 services: 下添加 ──
  litellm-proxy:
    image: docker.litellm.ai/berriai/litellm:main-stable
    mem_limit: 256m
    mem_reservation: 128m
    ports:
      - "4000:4000"
    volumes:
      - ./litellm/config.yaml:/app/config.yaml
    environment:
      LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY:?err}
      DASHSCOPE_API_KEY: ${DASHSCOPE_API_KEY:?err}
      DEEPSEEK_API_KEY: ${DEEPSEEK_API_KEY}
      LITELLM_LOG: WARNING
    command: ["--config", "/app/config.yaml", "--port", "4000", "--num_workers", "1"]
    restart: unless-stopped
```

- [ ] **1.3 在 `.env` 中添加新配置项**

```bash
# .env — 添加
LITELLM_MASTER_KEY=sk-test-123456
LLM_BASE_URL=http://litellm-proxy:4000
EMBEDDING_BASE_URL=http://litellm-proxy:4000

# DEEPSEEK_API_KEY 可选，切换到 DeepSeek 时填入
DEEPSEEK_API_KEY=
```

- [ ] **1.4 验证 Proxy 启动正常**

```bash
docker compose up -d litellm-proxy

# 验证健康检查
curl localhost:4000/health
# 期望: {"healthy": true} 或类似

# 验证鉴权 + 模型列表
curl -s -H "Authorization: Bearer sk-test-123456" localhost:4000/v1/models | python3 -m json.tool | head -20
# 期望: 返回模型列表，包含 qwen3.7-max / deepseek-v4-pro / qwen3.7-text-embedding / deepseek-embed-v4
```

- [ ] **1.5 Commit**

```bash
git add litellm/config.yaml docker-compose.yml .env
git commit -m "feat: add LiteLLM Proxy with DashScope and DeepSeek route config"
```

---

## Task 2: 模型配置重构

**Files:**
- Modify: `src/config/settings.py`
- Modify: `src/models.py`

**Interfaces:**
- Consumes: 环境变量 `LLM_MODEL`, `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_TEMPERATURE`, `LLM_KWARGS`, `EMBEDDING_MODEL`, `EMBEDDING_API_KEY`, `EMBEDDING_BASE_URL`, `EMBEDDING_DIMENSION`, `RERANK_MODEL`, `RERANK_API_KEY`
- Produces: `get_llm() -> ChatOpenAI`, `get_embeddings() -> OpenAIEmbeddings`, `get_rerank() -> DashScopeRerank`

- [ ] **2.1 重构 `src/config/settings.py`：添加新配置项 + fallback**

在文件末尾（`AUTH_TOKEN_TTL` 后面、或现有模型配置附近）添加：

```python
# ====== 模型配置（通用） ======
# 支持多 Provider：dashscope / deepseek / litellm_proxy
# 所有配置默认 fallback 到旧版 DashScope 配置项

# LLM 配置
LLM_MODEL: str = os.getenv("LLM_MODEL", "qwen3.7-max")
LLM_API_KEY: str = os.getenv("LLM_API_KEY") or os.getenv("DASHSCOPE_API_KEY", "")
LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "http://litellm-proxy:4000")
LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.1"))
LLM_KWARGS: str = os.getenv("LLM_KWARGS", "{}")

# Embedding 配置
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "qwen3.7-text-embedding")
EMBEDDING_API_KEY: str = os.getenv("EMBEDDING_API_KEY") or os.getenv("LLM_API_KEY") or os.getenv("DASHSCOPE_API_KEY", "")
EMBEDDING_BASE_URL: str = os.getenv("EMBEDDING_BASE_URL") or os.getenv("LLM_BASE_URL", "http://litellm-proxy:4000")

# Rerank 配置（固定走 DashScope）
RERANK_MODEL: str = os.getenv("RERANK_MODEL", "qwen3-rerank")
RERANK_API_KEY: str = os.getenv("RERANK_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or os.getenv("LLM_API_KEY", "")
```

注意：保留已有的 `DASHSCOPE_API_KEY` / `DASHSCOPE_BASE_URL` / `EMBEDDING_DIMENSION` / `EMBEDDING_BATCH_SIZE` 等旧配置，保持向后兼容。

- [ ] **2.2 重构 `src/models.py`：Factory 改为配置驱动**

替换 `get_embeddings()`、`get_llm()`、`get_rerank()` 三个函数：

```python
import json

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.document_compressors.dashscope_rerank import DashScopeRerank

from src.config import (
    LLM_MODEL, LLM_API_KEY, LLM_BASE_URL, LLM_TEMPERATURE, LLM_KWARGS,
    EMBEDDING_MODEL, EMBEDDING_API_KEY, EMBEDDING_BASE_URL, EMBEDDING_DIMENSION,
    RERANK_MODEL, RERANK_API_KEY,
)


def get_embeddings(model: str = EMBEDDING_MODEL) -> OpenAIEmbeddings:
    """创建文本向量化模型实例（配置驱动，通过 Proxy 或直连）。

    Args:
        model: 模型名称，默认 qwen3.7-text-embedding

    Returns:
        OpenAIEmbeddings 实例
    """
    return OpenAIEmbeddings(
        model=model,
        api_key=EMBEDDING_API_KEY,
        base_url=EMBEDDING_BASE_URL,
    )


def get_llm(
    model: str = LLM_MODEL,
    temperature: float = LLM_TEMPERATURE,
    **kwargs,
) -> ChatOpenAI:
    """创建大语言模型实例（配置驱动，通过 Proxy 或直连）。

    Args:
        model: 模型名称，默认 qwen3.7-max
        temperature: 温度参数
        **kwargs: 额外参数，可通过 LLM_KWARGS 环境变量传入

    Returns:
        ChatOpenAI 实例
    """
    extra_kwargs = json.loads(LLM_KWARGS)
    extra_kwargs.update(kwargs)
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        **extra_kwargs,
    )


def get_rerank(model: str = RERANK_MODEL, top_n: int = TOP_K_RERANK) -> DashScopeRerank:
    """创建文本重排序模型实例（固定走 DashScope）。

    Args:
        model: 模型名称，默认 qwen3-rerank
        top_n: 重排序后保留的文档数量

    Returns:
        DashScopeRerank 实例
    """
    return DashScopeRerank(
        model=model,
        top_n=top_n,
        dashscope_api_key=RERANK_API_KEY,
    )
```

注意顶部需要新增 import：

```python
import json
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
```

移除旧的 import（`DashScopeEmbeddings` 不再需要）：

```python
# 移除这行：
# from langchain_community.embeddings import DashScopeEmbeddings
```

移除 `FixedDimDashScopeEmbeddings` 类（不再使用，因为改用 `OpenAIEmbeddings`）。

- [ ] **2.3 验证：不依赖 Proxy 直连 DashScope**

```bash
# 临时改 .env 跳过 Proxy
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# 启动应用，发送一条测试消息
curl -X GET "http://localhost:8000/api/chat/stream?session_id=test&kb_id=some_kb_id&query=你好"
# 期望: 返回 SSE 流，最后以 [DONE] 结束，无报错
```

- [ ] **2.4 验证：切回 Proxy 地址**

```bash
# .env 恢复
LLM_BASE_URL=http://litellm-proxy:4000
EMBEDDING_BASE_URL=http://litellm-proxy:4000

# 确保 Proxy 已启动
docker compose up -d litellm-proxy

# 重发测试
curl -X GET "http://localhost:8000/api/chat/stream?session_id=test&kb_id=some_kb_id&query=你好"
# 期望: 同 2.3，正常返回
```

- [ ] **2.5 验证 Rerank 模型升级**

确认 `get_rerank()` 中 `dashscope_api_key=RERANK_API_KEY`，且 `RERANK_API_KEY` fallback 到 `DASHSCOPE_API_KEY`（非 `LLM_API_KEY`）。

通过应用发送一条需要检索的问题，检查日志：

```bash
curl -X GET "http://localhost:8000/api/chat/stream?session_id=test_rerank&kb_id=existing_kb_id&query=测试搜索"

# 日志应包含：
# "ChromaDB search: ..." ← 检索成功
# "Rerank completed: ..." ← rerank 成功
# 返回结果中有引用来源
```

- [ ] **2.6 Commit**

```bash
git add src/config/settings.py src/models.py
git commit -m "refactor: model factory with config-driven ChatOpenAI/OpenAIEmbeddings, upgrade rerank to qwen3-rerank"
```

---

## Task 3: KB 数据模型扩展

**Files:**
- Modify: `src/config/queries.py`
- Modify: `src/infra/db/entities/kb.py`
- Modify: `src/infra/db/mysql_db/kb_repo.py`

**Interfaces:**
- Produces: `KbListItem.description: str | None`，`KbRepo.get_all_kb() -> list[KbListItem]` 含 description

- [ ] **3.1 修改 SQL 查询，加 `description` 字段**

在 `src/config/queries.py` 中修改 `SELECT_ALL_KNOWLEDGE_BASES`：

```python
# 原：
# SELECT k.id, k.user_id, k.name, COUNT(d.id) AS doc_count

# 改为：
SELECT_ALL_KNOWLEDGE_BASES: str = """\
SELECT k.id, k.user_id, k.name, k.description, COUNT(d.id) AS doc_count
FROM knowledge_base k
LEFT JOIN document d ON d.kb_id = k.id AND d.status != 'deleted'
WHERE k.user_id = %s AND k.status != 'deleted'
GROUP BY k.id, k.user_id, k.name, k.description
ORDER BY k.created_at DESC
"""
```

- [ ] **3.2 修改 `KbListItem` 实体，加 `description` 字段**

在 `src/infra/db/entities/kb.py` 中：

```python
@dataclass(slots=True)
class KbListItem:
    id: str
    user_id: str
    name: str
    description: str | None = None  # ← 新增
    doc_count: int = 0
```

- [ ] **3.3 修改 `KbRepo.get_all_kb()` 读取 `description`**

在 `src/infra/db/mysql_db/kb_repo.py` 中：

```python
result = [
    KbListItem(
        id=row["id"],
        user_id=row["user_id"],
        name=row["name"],
        description=row.get("description"),  # ← 新增
        doc_count=row["doc_count"],
    )
    for row in await cursor.fetchall()
]
```

- [ ] **3.4 验证查询正常**

```python
# 通过 Python 交互式 shell 或临时脚本验证
from src.infra.db.mysql_db import MySQLDB, KbRepo
import asyncio

async def test():
    repo = KbRepo(MySQLDB())
    kbs = await repo.get_all_kb("some_user_id")
    for kb in kbs:
        print(f"{kb.name}: {kb.description}")

asyncio.run(test())
# 期望: 正常打印出知识库名称和描述
```

- [ ] **3.5 Commit**

```bash
git add src/config/queries.py src/infra/db/entities/kb.py src/infra/db/mysql_db/kb_repo.py
git commit -m "feat: add description field to KbListItem for KB routing"
```

---

## Task 4: 知识库智能路由核心逻辑

**Files:**
- Create: `src/rag/kb_router.py`

**Interfaces:**
- Produces: `KBRouter` 类，`route(query: str, kb_list: list[KbListItem]) -> list[str]` 返回匹配的 kb_id 列表

- [ ] **4.1 确认 `numpy` 在依赖中**

```bash
# 检查 pyproject.toml 或 requirements.txt 中是否有 numpy
grep -n "numpy" pyproject.toml requirements.txt 2>/dev/null || echo "not found"

# 如果不在，加一条：
# 在 pyproject.toml 的 dependencies 中添加：
# "numpy>=1.24",
```

- [ ] **4.2 创建 `src/rag/kb_router.py`**

```python
"""知识库智能路由模块 — 语义路由 + LLM 兜底。

当用户选择"所有知识库"时，先用 Embedding 相似度匹配最相关的 1-2 个 KB，
低置信度时使用 LLM 进行分类兜底。
"""

import numpy as np
from typing import Optional
from loguru import logger

from src.infra.db.entities.kb import KbListItem


# 语义路由相似度阈值：低于此值触发 LLM 兜底
SEMANTIC_THRESHOLD = 0.82


class KBRouter:
    """知识库路由器 — 根据用户查询匹配最相关的知识库。

    用法:
        router = KBRouter(embed_fn, llm)
        matched_ids = router.route(query, kb_list)
    """

    def __init__(self, embed_fn, llm=None):
        """初始化路由器。

        Args:
            embed_fn: 嵌入函数，需实现 embed_query(text) -> list[float]
            llm: 可选，LLM 实例（用于低置信度兜底），需实现 invoke() 接口
        """
        self._embed_fn = embed_fn
        self._llm = llm

    def route(self, query: str, kb_list: list[KbListItem]) -> list[str]:
        """执行路由决策，返回匹配的 kb_id 列表。

        策略：
        1. 计算 query 与每个 KB name+description 的余弦相似度
        2. 取 top-1 相似度
        3. top-1 相似度 >= 阈值 → 取 top-2 KB IDs
        4. top-1 相似度 < 阈值 → LLM 兜底（如 llm 可用）
        5. LLM 兜底失败或无 llm → 返回空列表（外层降级为全量检索）

        Args:
            query: 用户查询文本
            kb_list: 用户的所有知识库列表（含 name 和 description）

        Returns:
            list[str]: 匹配的 kb_id 列表，空列表表示未命中（需降级）
        """
        if not kb_list:
            logger.info("KBRouter: empty kb_list, returning []")
            return []

        # ── 1. 构建每个 KB 的 route_key ──
        route_keys = []
        for kb in kb_list:
            key = kb.name
            if kb.description:
                key = f"{kb.name}: {kb.description}"
            route_keys.append(key)

        # ── 2. 计算 query embedding ──
        query_vec = np.array(self._embed_fn.embed_query(query))

        # ── 3. 计算每个 KB 的 embedding（批量）──
        # 此处不缓存，每次实时计算（KB 数量少时可接受）
        kb_vecs = []
        for key in route_keys:
            vec = np.array(self._embed_fn.embed_query(key))
            kb_vecs.append(vec)

        # ── 4. 计算余弦相似度 ──
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            logger.warning("KBRouter: query embedding is zero vector")
            return self._llm_fallback(query, kb_list)

        similarities = []
        for vec in kb_vecs:
            vec_norm = np.linalg.norm(vec)
            if vec_norm == 0:
                similarities.append(0.0)
            else:
                sim = float(np.dot(query_vec, vec) / (query_norm * vec_norm))
                similarities.append(sim)

        # ── 5. 排序 ──
        ranked = sorted(
            zip(kb_list, similarities),
            key=lambda x: x[1],
            reverse=True,
        )

        top_kb, top_score = ranked[0]
        logger.info(
            "KBRouter semantic: top={}({}) score={:.4f} threshold={}",
            top_kb.name, top_kb.id[:8], top_score, SEMANTIC_THRESHOLD,
        )

        if top_score >= SEMANTIC_THRESHOLD:
            # 取 top-2
            matched = [item[0].id for item in ranked[:2]]
            logger.info("KBRouter: semantic match -> {}", matched)
            return matched

        # ── 6. 低置信度 → LLM 兜底 ──
        return self._llm_fallback(query, kb_list)

    def _llm_fallback(
        self, query: str, kb_list: list[KbListItem]
    ) -> list[str]:
        """使用 LLM 分类查询归属的知识库。

        Returns:
            list[str]: LLM 选中的 kb_id 列表（最多 2 个），
                       出错或无 llm 时返回空列表
        """
        if not self._llm:
            logger.info("KBRouter: no LLM available, returning []")
            return []

        kb_options = "\n".join(
            f"- {kb.id}: {kb.name}" + (f" ({kb.description})" if kb.description else "")
            for kb in kb_list
        )

        prompt = f"""根据用户问题，选择最相关的 1-2 个知识库。
只返回知识库 ID，多个用逗号分隔，不要包含其他内容。

可选知识库：
{kb_options}

用户问题：{query}"""

        try:
            # 使用结构简单的 LLM 调用
            from langchain_core.messages import HumanMessage, SystemMessage
            response = self._llm.invoke([
                SystemMessage(content="你是一个知识库路由专家。返回最相关的知识库 ID。"),
                HumanMessage(content=prompt),
            ])
            raw = response.content.strip()
            # 解析逗号分隔的 ID
            ids = [id_str.strip() for id_str in raw.split(",") if id_str.strip()]
            # 验证 ID 是否在 kb_list 中
            valid_ids = {kb.id for kb in kb_list}
            matched = [id_str for id_str in ids if id_str in valid_ids][:2]
            logger.info("KBRouter: LLM fallback -> {}", matched)
            return matched
        except Exception as e:
            logger.warning("KBRouter: LLM fallback failed: {}", e)
            return []
```

- [ ] **4.3 验证路由逻辑**

```python
# 通过 Python 交互式 shell 验证
from src.rag.kb_router import KBRouter
from src.infra.db.entities.kb import KbListItem

# mock embed_fn
class MockEmbed:
    def embed_query(self, text):
        # 返回固定维度向量，仅测试逻辑
        import numpy as np
        np.random.seed(len(text))
        return list(np.random.randn(4))

embed_fn = MockEmbed()
router = KBRouter(embed_fn)

kbs = [
    KbListItem(id="kb-fin", name="财务数据", description="财务报表和营收数据"),
    KbListItem(id="kb-tech", name="技术文档", description="产品技术规格"),
]

result = router.route("今年营收多少", kbs)
print(f"路由结果: {result}")
# 期望: 返回 ["kb-fin"] 或 ["kb-fin", ...]
```

- [ ] **4.4 Commit**

```bash
git add src/rag/kb_router.py
git commit -m "feat: add KBRouter with semantic routing and LLM fallback"
```

---

## Task 5: KB 路由节点接入 LangGraph

**Files:**
- Modify: `src/agents/graph/state.py`
- Modify: `src/agents/graph/nodes.py`
- Modify: `src/agents/graph/workflow.py`

**Interfaces:**
- Consumes: `KBRouter`, `AgentState`, `get_embeddings()`, `get_llm()`
- Produces: 修改后的 StateGraph，含 kb_router_node

- [ ] **5.1 在 `AgentState` 中添加 `_resolved_kb_ids` 字段**

在 `src/agents/graph/state.py` 中：

```python
@dataclass
class AgentState:
    # ... 现有字段 ...

    # ── 路由控制 ──
    _resolved_kb_ids: list[str] | None = None
    # None = 未路由 / 降级全量；[...] = 路由选中的 KB ID 列表
```

- [ ] **5.2 在 `nodes.py` 中添加 `make_kb_router_node` 工厂函数**

在 `src/agents/graph/nodes.py` 中，在 `classify_node` 之前添加：

```python
def make_kb_router_node(embed_fn, llm) -> Callable:
    """创建 KB 路由节点工厂函数。

    当 kb_id 为空（"所有知识库"）时，使用 KBRouter 智能匹配 KB。
    当 kb_id 非空时直接穿透。
    """
    from src.rag.kb_router import KBRouter

    router = KBRouter(embed_fn, llm)

    async def kb_router_node(state: AgentState) -> dict:
        # kb_id 非空 → 穿透
        if state.kb_id:
            return {"_resolved_kb_ids": [state.kb_id]}

        # kb_id 为空 → 路由
        from src.infra.llm.trace_context import current_user_id
        from src.infra.db.mysql_db import KbRepo, MySQLDB

        uid = current_user_id.get()
        if not uid:
            logger.info("kb_router_node: no user_id, fallback to all")
            return {"_resolved_kb_ids": None}

        kbs = await KbRepo(MySQLDB()).get_all_kb(uid)
        kb_ids = router.route(state.query, kbs)
        logger.info(
            "kb_router_node: query={} kb_count={} routed={}",
            state.query[:40], len(kbs), kb_ids,
        )
        return {"_resolved_kb_ids": kb_ids if kb_ids else None}

    return kb_router_node
```

- [ ] **5.3 在 `nodes.py` 中修改 `make_retrieve_node` 使用 `_resolved_kb_ids`**

修改 `retrieve_node` 函数。注意 Hybrid Search 保护：多 KB 路由时跳过 BM25，只走纯向量检索：

```python
async def retrieve_node(state: AgentState) -> dict:
    q = state.rewritten_query or state.query
    resolved_ids = state._resolved_kb_ids or state.kb_id
    # resolved_ids 可以是 str | list[str] | None
    # None 的情况：retrieval.py 中 get_all_kb 全量搜索
    logger.info("retrieve_node start: query={} kb_ids={}", q[:50], resolved_ids)

    # 多 KB 路由时跳过 Hybrid Search（BM25 不支持 list[str]）
    if isinstance(resolved_ids, list) and len(resolved_ids) > 1:
        results = await asyncio.to_thread(
            vector_store.similarity_search, resolved_ids, q, k=TOP_K_RETRIEVAL
        )
    else:
        results = await search(q, resolved_ids, vector_store, bm25)

    if results is None:
        results = []
    logger.info("retrieve_node done: results={}", len(results))
    return {"retrieval_results": results}
```

注意：需要在文件顶部加 `import asyncio`。

- [ ] **5.4 在 `workflow.py` 中注册 `kb_router_node` 和对应边**

在 `build_graph()` 中：

```python
def build_graph(vector_store, bm25, llm, reranker, prompt_manager):
    builder = StateGraph(AgentState)

    # 注册节点 — kb_router 为入口节点
    builder.add_node("kb_router", make_kb_router_node(
        embed_fn=embed_fn,  # ← 需要传入 embed_fn
        llm=llm,
    ))
    builder.add_node(LangGraphNode.Classify.NAME, classify_node)
    # ... 其余节点不变 ...

    # 设置入口点和边 — kb_router → classify → ...
    builder.set_entry_point("kb_router")
    builder.add_edge("kb_router", LangGraphNode.Classify.NAME)
    # ... 后续边不变 ...
```

注意：`build_graph` 函数签名需要新增 `embed_fn` 参数：

```python
def build_graph(
    vector_store: VectorStore,
    bm25: BM25Index | None,
    llm,
    reranker,
    embed_fn,          # ← 新增
    prompt_manager,
) -> CompiledStateGraph:
```

同时在 `agent_service.py` 中调用 `build_graph` 时传入 `embed_fn`：

```python
# agent_service.py 中
from src.models import get_embeddings

self._graph = build_graph(
    vector_store,
    bm25,
    self._llm,
    self._reranker,
    get_embeddings(),  # ← 新增
    self._prompt_manager,
)
```

- [ ] **5.5 验证路由生效**

```bash
# 通过日志验证
curl -X GET "http://localhost:8000/api/chat/stream?session_id=test&kb_id=&query=财务数据查询"

# 检查日志应包含：
# "kb_router_node: ... routed=['kb-xxx', 'kb-yyy']"
# "retrieve_node: ... kb_ids=['kb-xxx', 'kb-yyy']"
# 而不是所有 KB 的 ID
```

- [ ] **5.6 Commit**

```bash
git add src/agents/graph/state.py src/agents/graph/nodes.py src/agents/graph/workflow.py src/services/agent_service.py
git commit -m "feat: add kb_router_node to LangGraph workflow"
```

---

## Task 6: 清理与兼容

**Files:**
- Modify: `.env.example`
- Modify: `README.md`

- [ ] **6.1 更新 `.env.example`**

```bash
# .env.example — 添加新配置项说明
LITELLM_MASTER_KEY=sk-test-123456

# 模型配置（LLM / Embedding / Rerank 各自独立）
LLM_MODEL=qwen3.7-max
LLM_API_KEY=
LLM_BASE_URL=http://litellm-proxy:4000
LLM_TEMPERATURE=0.1
# LLM_KWARGS={"extra_body": {"enable_thinking": false}}

EMBEDDING_MODEL=qwen3.7-text-embedding
EMBEDDING_API_KEY=
EMBEDDING_BASE_URL=http://litellm-proxy:4000

RERANK_MODEL=qwen3-rerank
RERANK_API_KEY=

# 兼容旧配置（如只配了 DASHSCOPE_API_KEY，会自动作为各模型的 fallback）
DASHSCOPE_API_KEY=
DEEPSEEK_API_KEY=
```

- [ ] **6.2 更新 README 中的模型配置说明**

在 README 中相关章节更新配置说明，重点包括：
1. LiteLLM Proxy 的定位和作用（统一网关）
2. 三类模型独立配置的方法
3. 切换 Provider 的操作步骤（改 .env + restart litellm-proxy）
4. Embedding 切换的风险说明（需要 re-index）
5. 本地开发直接用 `docker compose up -d` 一键启动

- [ ] **6.3 确认旧配置项引用无遗漏**

```bash
grep -rn "DASHSCOPE_API_KEY" src/ --include="*.py" | grep -v __pycache__
# 期望: 只在 settings.py 中有定义，其他模块通过 config 导入使用
```

- [ ] **6.4 Commit**

```bash
git add .env.example README.md
git commit -m "docs: update env example and README with multi-provider config"
```

---

## 验证清单

实施完成后，按以下场景验证：

1. **Proxy 健康检查**: `curl localhost:4000/health` 返回 200
2. **模型列表**: `curl -H "Authorization: Bearer sk-test-123456" localhost:4000/v1/models` 返回配置的模型
3. **LLM 直连**: `LLM_BASE_URL` 设为 DashScope 直连地址，系统正常问答
4. **LLM Proxy**: `LLM_BASE_URL` 恢复 Proxy 地址，系统正常问答
5. **Embedding**: 文档上传 → 处理 → 检索 → 完整性正常
6. **Rerank**: 日志中出现 "Rerank completed" 且无报错
7. **知识库路由**: 选"所有知识库"，日志中出现 "kb_router_node" 且只检索了 top-2 KB
8. **路由降级**: 模糊查询触发 LLM 兜底或全量检索，不崩溃
9. **配置向后兼容**: 只留 `DASHSCOPE_API_KEY`，删掉其他 Key，系统正常启动
