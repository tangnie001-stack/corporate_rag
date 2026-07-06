"""Prompt 管理器 — 从 Langfuse 拉取 prompt，不可用时兜底到本地配置。

使用方式：
    manager = PromptManager(secret_key, public_key, host)
    sys_prompt = manager.get_system_prompt()  # 尝试 Langfuse → 兜底本地
    user_tmpl = manager.get_user_template(input_data)  # 同上
"""

import json
import logging
import time
from typing import Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

# 本地兜底的 prompt 常量（与 src/config/prompts.py 一致）
_INLINE_CITATION_INSTRUCTION: str = (
    '\n引用文档时请在句末标注编号 [1][2]，例如："营收3943亿元[1]"。\n'
)

from src.config.prompts import FINANCIAL_SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

_FALLBACK_SYSTEM_PROMPT: str = FINANCIAL_SYSTEM_PROMPT + _INLINE_CITATION_INSTRUCTION
_FALLBACK_USER_TEMPLATE: str = USER_PROMPT_TEMPLATE


class PromptManager:
    """从 Langfuse 拉取 prompt，带缓存和本地兜底。

    Args:
        secret_key: Langfuse Secret Key
        public_key: Langfuse Public Key
        host: Langfuse 服务器地址
        cache_ttl: 缓存有效期（秒），默认 60
    """

    PROMPT_NAMES = {
        "system": "financial-system-prompt",
        "user": "user-prompt-template",
    }

    def __init__(
        self,
        cache_ttl: int = 60,
    ) -> None:
        """从环境变量读取 Langfuse 配置，失败时兜底本地 prompt。

        Args:
            cache_ttl: 缓存有效期（秒），默认 60 秒
        """
        from src.config import LANGFUSE_SECRET_KEY, LANGFUSE_PUBLIC_KEY, LANGFUSE_HOST
        import base64

        self._auth = base64.b64encode(
            f"{LANGFUSE_PUBLIC_KEY}:{LANGFUSE_SECRET_KEY}".encode()
        ).decode()
        self._host = LANGFUSE_HOST.rstrip("/")
        self._cache_ttl = cache_ttl
        self._cache: dict[str, tuple[str, float]] = {}

    def _fetch_prompt(self, name: str) -> Optional[str]:
        """从 Langfuse API 获取 prompt 文本，失败返回 None。

        使用 HTTP Basic Auth 认证，请求 /api/public/v2/prompts/{name} 端点。
        网络/解析失败均返回 None，由上层兜底到本地 prompt。

        Args:
            name: Langfuse 上的 prompt 名称

        Returns:
            prompt 文本字符串，失败时返回 None
        """
        url = f"{self._host}/api/public/v2/prompts/{name}"
        try:
            req = Request(url)
            req.add_header("Authorization", f"Basic {self._auth}")
            resp = urlopen(req, timeout=5)
            data = json.loads(resp.read())
            prompt_text: str = data.get("prompt", "")
            if prompt_text:
                logger.info(
                    "Fetched prompt '{}' from Langfuse (v{})", name, data.get("version")
                )
            return prompt_text
        except URLError as e:
            logger.warning("Failed to fetch prompt '{}' from Langfuse: {}", name, e)
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Invalid response for prompt '{}': {}", name, e)
        return None

    def _get(self, name: str, fallback: str) -> str:
        """带缓存的获取逻辑：缓存未命中或过期 → 拉取 Langfuse → 兜底。

        缓存 key 为 prompt 名称，缓存过期后重新拉取。
        如果 Langfuse 不可用，使用 fallback 参数作为兜底文本。

        Args:
            name: Langfuse prompt 名称
            fallback: 本地兜底 prompt 文本

        Returns:
            prompt 文本字符串
        """
        now = time.time()
        # 检查缓存
        if name in self._cache:
            prompt_text, expiry = self._cache[name]
            if now < expiry:
                return prompt_text

        # 从 Langfuse 拉取
        prompt_text = self._fetch_prompt(name)
        if prompt_text:
            self._cache[name] = (prompt_text, now + self._cache_ttl)
            return prompt_text

        # 兜底到本地
        logger.info("Using fallback prompt for '{}'", name)
        self._cache[name] = (fallback, now + self._cache_ttl)
        return fallback

    def get_system_prompt(self) -> str:
        """获取系统指令 prompt，追加内联引用编号指令。

        从 Langfuse 拉取或使用本地兜底的 financial-system-prompt，
        确保末尾始终包含内联引用编号指令。

        Returns:
            完整的系统 prompt 文本
        """
        prompt = self._get(self.PROMPT_NAMES["system"], _FALLBACK_SYSTEM_PROMPT)
        # 确保内联引用指令始终存在（无论 prompt 来自 Langfuse 还是本地兜底）
        if _INLINE_CITATION_INSTRUCTION not in prompt:
            prompt += _INLINE_CITATION_INSTRUCTION
        return prompt

    def get_user_template(self, context: str = "", query: str = "") -> str:
        """获取用户消息模板并填充占位符。

        从 Langfuse 拉取或使用本地兜底的 user-prompt-template，
        用 context 和 query 替换模板中的 {context} 和 {query} 占位符。

        Args:
            context: 检索到的文档上下文文本
            query: 用户查询文本

        Returns:
            填充后的用户消息 prompt 文本
        """
        template = self._get(self.PROMPT_NAMES["user"], _FALLBACK_USER_TEMPLATE)
        return template.format(context=context, query=query)

    def invalidate_cache(self) -> None:
        """清空缓存，下次调用会重新拉取。

        在 Langfuse prompt 版本更新后调用，强制重新获取最新版本。
        """
        self._cache.clear()
        logger.debug("Prompt cache cleared")
