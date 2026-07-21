"""全局配置模块 — 集中管理所有环境变量和系统参数。

本模块从 .env 文件和环境变量中读取配置项，供其他模块统一导入使用。
所有配置项均提供合理的默认值，方便本地开发和 Docker 部署两套环境无缝切换。

主要配置分区：
  - DashScope API：阿里云大模型平台认证与模型选择
  - MySQL：元数据库连接参数
  - Redis：对话历史缓存参数
  - ChromaDB：向量数据库持久化路径
  - 文档处理：分块策略（chunk size/overlap）和检索参数
  - 对话管理：历史窗口大小和缓存过期时间
  - 重试策略：外部调用的指数退避参数
  - 文件上传：单文件大小限制
"""

import os
from dotenv import load_dotenv

# 加载项目根目录的 .env 文件（如存在），将键值对注入 os.environ
load_dotenv()


# ====== DashScope API ======
# 阿里云 DashScope 平台 API Key，必填项，用于调用 LLM / Embedding / Rerank 模型
DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")
# DashScope OpenAI 兼容接口地址，qwen 系列模型走此 URL
DASHSCOPE_BASE_URL: str = os.getenv(
    "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)

# ====== 模型选择 ======
# 大语言模型：用于生成最终回答，qwen-max 效果最佳
LLM_MODEL: str = os.getenv("LLM_MODEL", "qwen3.7-max")
# 向量化模型：将文本转为向量，用于 ChromaDB 语义检索
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "qwen3.7-text-embedding")
# 向量输出维度：固定维度后切换模型无需重建 ChromaDB collection
EMBEDDING_DIMENSION: int = int(os.getenv("EMBEDDING_DIMENSION", "1024"))
# 重排序模型：对检索结果二次打分排序，提高最终送入 LLM 的上下文质量
RERANK_MODEL: str = os.getenv("RERANK_MODEL", "gte-rerank-v1")
# LLM 温度参数：越低回答越确定性（适合金融场景），0.1 几乎不产生随机性
LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.1"))
# Embedding API 单次 batch 上限（DashScope 限制 20 条，超出需分批）
EMBEDDING_BATCH_SIZE: int = int(os.getenv("EMBEDDING_BATCH_SIZE", "20"))
# RAGAS 评估专用模型（独立于生产 LLM，temperature 固定为 0）
# 不可使用推理模型（如 qwen3.7-max），RAGAS 内部会传 n>1 参数，
# 推理模型要求 n=1 会导致 BadRequestError。用非推理模型如 qwen-plus 系列。
# 为空时回退到 LLM_MODEL
RAGAS_LLM_MODEL: str = os.getenv("RAGAS_LLM_MODEL", "qwen3.7-plus-2026-05-26")
# RAGAS 测试集生成条数
RAGAS_TEST_SIZE: int = int(os.getenv("RAGAS_TEST_SIZE", "20"))
# RAGAS 测试集存储目录
RAGAS_DATA_DIR: str = os.getenv("RAGAS_DATA_DIR", "data/ragas")
# RAGAS LLM 缓存目录（DiskCacheBackend，用于提速重复生成）
RAGAS_LLM_CACHE_DIR: str = os.getenv("RAGAS_LLM_CACHE_DIR", "data/ragas/llm_cache")
# RAGAS 默认用户 ID（查询知识库时使用）
RAGAS_USER_ID: str = os.getenv("RAGAS_USER_ID", "24a93c0e-3c9b-4d8d-a371-2d8b3607892a")
# RAGAS 文档白名单：只处理白名单中的文档 ID
# 用于跳过不需要参与测试集生成的文档（如扫描件、不相关文档）
RAGAS_DOC_WHITELIST: list[str] = [
    "fa7d700e-f093-45be-a78f-73fbdfd1801d",   # neusoft_2025_q1.pdf
]

# ====== MySQL ======
# 元数据库，存储 knowledge_base（知识库）和 document（文档）的元信息
MYSQL_HOST: str = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT: int = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER: str = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD: str = os.getenv("MYSQL_PASSWORD", "financial_qa_pass")
MYSQL_DATABASE: str = os.getenv("MYSQL_DATABASE", "financial_qa")

# ====== Redis ======
# 对话历史缓存，支持会话级上下文记忆；连接失败时自动降级为内存存储
REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB: int = int(os.getenv("REDIS_DB", "0"))
REDIS_USERNAME: str = os.getenv("REDIS_USERNAME", "default")
REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "financial_qa_pass")
# 拼接完整 Redis URL，供 redis-py 的 from_url() 直接使用
REDIS_URL: str = (
    f"redis://{REDIS_USERNAME}:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
)

# ====== ChromaDB ======
# 向量数据库服务地址（Docker 容器内用容器名，开发环境用 localhost）
CHROMA_HOST: str = os.getenv("CHROMA_HOST", "localhost")
# 向量数据库服务端口（ChromaDB 默认 8000）
CHROMA_PORT: int = int(os.getenv("CHROMA_PORT", "8000"))
# collection 名称前缀，每个知识库对应一个 collection（如 kb_<uuid>）
CHROMA_COLLECTION_PREFIX: str = os.getenv("CHROMA_COLLECTION_PREFIX", "kb_")
# （已废弃）向量数据库持久化目录 — 改用独立 ChromaDB 容器后不再需要本地路径
CHROMA_PERSIST_DIR: str = os.getenv("CHROMA_PERSIST_DIR", "./data/chroma_persist")

# ====== 文档处理 ======
# 文本分块大小（字符数）：512 是金融文档的平衡点，太小丢上下文，太大检索不精准
CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "512"))
# 相邻分块的重叠字符数：保证跨块的句子不会丢失关键信息
CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "64"))
# 初步检索返回的 top-K 数量（送入 reranker 之前的候选数）
TOP_K_RETRIEVAL: int = int(os.getenv("TOP_K_RETRIEVAL", "50"))
# 重排序后保留的 top-N 数量（最终送入 LLM 的上下文数量）
TOP_K_RERANK: int = int(os.getenv("TOP_K_RERANK", "5"))
# 扫描件检测阈值：单页可提取文字少于 200 字符视为"扫描页"
MIN_TEXT_CHARS: int = int(os.getenv("MIN_TEXT_CHARS", "200"))
# 页眉页脚排除阈值：距离页面顶部/底部 N px 内的文本块视为页眉页脚
HEADER_FOOTER_MARGIN: int = int(os.getenv("HEADER_FOOTER_MARGIN", "80"))
# 跨页表格合并阈值：TABLE → 短文本（< N 字符）→ TABLE 合并为一个 chunk
CROSS_PAGE_TABLE_MERGE_THRESHOLD: int = int(
    os.getenv("CROSS_PAGE_TABLE_MERGE_THRESHOLD", "100")
)
# 表格合并后最大 token 数（超过则不合并，避免 embedding 截断丢失信息）
# 使用时需 *2 转为字符数（中文 1 token ≈ 2 字符），如 2048 token → 4096 chars
MAX_TABLE_TOKENS: int = int(os.getenv("MAX_TABLE_TOKENS", "2048"))

# ====== Hybrid Search ======
# 是否启用 BM25 + Dense 混合检索（通过 RRF 融合）
HYBRID_SEARCH_ENABLED: bool = (
    os.getenv("HYBRID_SEARCH_ENABLED", "true").lower() == "true"
)
# BM25 索引持久化根目录（每个知识库独立子目录）
BM25_INDEX_DIR: str = os.getenv("BM25_INDEX_DIR", "data/bm25_index")

# ====== 对话管理 ======
# 对话历史窗口大小：取最近 N 条消息作为 LLM 的上下文，避免 token 溢出
MEMORY_WINDOW: int = int(os.getenv("MEMORY_WINDOW", "6"))
# Redis 中对话历史的过期时间（秒），默认 7 天
REDIS_TTL: int = int(os.getenv("REDIS_TTL", "604800"))

# ====== 重试策略 ======
# 外部调用（DashScope / MySQL / Redis）失败时的指数退避参数
# 重试 3 次，初始间隔 1s，每次翻倍：1s → 2s → 4s
RETRY_MAX_ATTEMPTS: int = int(os.getenv("RETRY_MAX_ATTEMPTS", "3"))
RETRY_INITIAL_INTERVAL: float = float(os.getenv("RETRY_INITIAL_INTERVAL", "1.0"))
RETRY_BACKOFF_FACTOR: float = float(os.getenv("RETRY_BACKOFF_FACTOR", "2.0"))

# ====== 文件上传 ======
# 单文件上传大小上限（字节），默认 50MB
MAX_FILE_SIZE: int = int(os.getenv("MAX_FILE_SIZE", str(10 * 1024 * 1024)))

# ====== MinIO ======
# 对象存储服务，用于持久化原始文档文件
MINIO_ENDPOINT: str = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY: str = os.getenv("MINIO_ACCESS_KEY", "minio")
MINIO_SECRET_KEY: str = os.getenv("MINIO_SECRET_KEY", "miniosecret")
MINIO_DOC_BUCKET: str = os.getenv("MINIO_DOC_BUCKET", "documents")

# ====== Auth ======
# 认证 Token 过期时间（秒），默认 30 天
AUTH_TOKEN_TTL: int = int(os.getenv("AUTH_TOKEN_TTL", "2592000"))

# ====== Langfuse ======
# LLM 可观测性平台配置，用于 trace 检索→重排序→生成的完整链路
# 首次启动需手动在 Langfuse UI (http://localhost:3000) 创建 API Key
LANGFUSE_SECRET_KEY: str = os.getenv(
    "LANGFUSE_SECRET_KEY", "sk-lf-8665d453-271d-4ce2-9f3b-5b471dad5ce2"
)
LANGFUSE_PUBLIC_KEY: str = os.getenv(
    "LANGFUSE_PUBLIC_KEY", "pk-lf-96995ff8-f6e4-4205-b02d-eba6e5ed94c8"
)
# 注意：Docker 内部使用容器名 langfuse:3000，宿主机访问用 localhost:3000
LANGFUSE_HOST: str = os.getenv("LANGFUSE_HOST", "http://langfuse:3000")
# 全局开关：false 时完全跳过 Langfuse 初始化
LANGFUSE_ENABLE: bool = os.getenv("LANGFUSE_ENABLE", "true").lower() == "true"

# ====== 分块质量评估 ======
# 分块质量评估开关：true 时上传文件后自动跑 3 个质量指标
# 默认关闭，不影响现有流程
CHUNK_EVAL_ENABLED: bool = os.getenv("CHUNK_EVAL_ENABLED", "true").lower() == "true"
