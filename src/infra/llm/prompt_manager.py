"""Prompt 管理器 — 从 Langfuse 拉取 prompt，不可用时兜底到本地配置。

使用方式：
    manager = PromptManager(secret_key, public_key, host)
    sys_prompt = manager.get_system_prompt()  # 尝试 Langfuse → 兜底本地
    user_tmpl = manager.get_user_template(input_data)  # 同上
"""

import json
import time
from datetime import UTC, datetime
from typing import ClassVar
from urllib.error import URLError
from urllib.request import Request, urlopen

from loguru import logger

from src.config.prompts import (
    CLASSIFIER_SYSTEM_PROMPT,
    CLASSIFIER_USER_TEMPLATE,
    FINANCIAL_SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
)

# 本地兜底的 prompt 常量（与 src/config/prompts.py 一致）
_INLINE_CITATION_INSTRUCTION: str = (
    '\n引用文档时请在句末标注编号 [1][2]，例如："营收3943亿元[1]"。\n'
)

_FALLBACK_SYSTEM_PROMPT: str = FINANCIAL_SYSTEM_PROMPT + _INLINE_CITATION_INSTRUCTION
_FALLBACK_USER_TEMPLATE: str = USER_PROMPT_TEMPLATE


def _with_current_date(prompt: str) -> str:
    """在系统提示词末尾追加今日日期，锚定相对时间表达（本报告期/今年）。

    重复调用时若日期行已存在则直接返回，保证幂等。
    日期在 get_system_prompt 层追加而非存入缓存，避免 _get() 60s 缓存跨天返回旧日期。

    Args:
        prompt: 原始系统提示词文本

    Returns:
        追加今日日期行后的提示词文本
    """
    today = datetime.now(UTC).date()
    date_line = f"\n今天是 {today.year}年{today.month}月{today.day}日。\n"
    if date_line.strip() in prompt:
        return prompt
    return prompt + date_line


class PromptManager:
    """从 Langfuse 拉取 prompt，带缓存和本地兜底。

    Args:
        secret_key: Langfuse Secret Key
        public_key: Langfuse Public Key
        host: Langfuse 服务器地址
        cache_ttl: 缓存有效期（秒），默认 60
    """

    PROMPT_NAMES: ClassVar[dict[str, str]] = {
        "system": "financial-system-prompt",
        "user": "user-prompt-template",
        "classifier": "classifier-prompt",
    }

    def __init__(
        self,
        cache_ttl: int = 60,
    ) -> None:
        """从环境变量读取 Langfuse 配置，失败时兜底本地 prompt。

        Args:
            cache_ttl: 缓存有效期（秒），默认 60 秒
        """
        import base64

        from src.config import (
            LANGFUSE_ENABLE,
            LANGFUSE_HOST,
            LANGFUSE_PUBLIC_KEY,
            LANGFUSE_SECRET_KEY,
        )

        self._enabled = LANGFUSE_ENABLE
        if self._enabled:
            self._auth = base64.b64encode(
                f"{LANGFUSE_PUBLIC_KEY}:{LANGFUSE_SECRET_KEY}".encode()
            ).decode()
            self._host = LANGFUSE_HOST.rstrip("/")
        else:
            self._auth = ""
            self._host = ""
        self._cache_ttl = cache_ttl
        self._cache: dict[str, tuple[str, float]] = {}

    def _fetch_prompt(self, name: str) -> str | None:
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

        # 从 Langfuse 拉取（关闭时跳过）
        if self._enabled:
            prompt_text = self._fetch_prompt(name)
            if prompt_text:
                self._cache[name] = (prompt_text, now + self._cache_ttl)
                return prompt_text

        # 兜底到本地
        logger.info("Using fallback prompt for '{}'", name)
        self._cache[name] = (fallback, now + self._cache_ttl)
        return fallback

    def get_system_prompt(self) -> str:
        """获取系统指令 prompt，追加内联引用编号指令和今日日期。

        从 Langfuse 拉取或使用本地兜底的 financial-system-prompt，
        确保末尾始终包含内联引用编号指令，并追加今日日期锚定相对时间表达。

        Returns:
            完整的系统 prompt 文本
        """
        prompt = self._get(self.PROMPT_NAMES["system"], _FALLBACK_SYSTEM_PROMPT)
        # 确保内联引用指令始终存在（无论 prompt 来自 Langfuse 还是本地兜底）
        if _INLINE_CITATION_INSTRUCTION not in prompt:
            prompt += _INLINE_CITATION_INSTRUCTION
        return _with_current_date(prompt)

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

    def get_classifier_prompt(
        self,
        query: str,
        entities: str,
        complexity_score: float,
        history: str,
    ) -> str:
        """获取分类器 prompt，从 Langfuse 拉取或兜底本地模板。

        拼接系统提示词和填充后的用户消息模板，返回完整的 prompt 文本。
        由调用方自行封装为 SystemMessage / HumanMessage，本层不耦合 LangChain。

        Args:
            query: 用户原始查询文本
            entities: 已提取实体列表（字符串）
            complexity_score: 规则预判的复杂度评分
            history: 最近对话历史文本

        Returns:
            完整的分类器 prompt 文本（系统提示 + 用户消息）
        """
        sys_prompt = self._get(
            self.PROMPT_NAMES["classifier"], CLASSIFIER_SYSTEM_PROMPT
        )
        user_prompt = CLASSIFIER_USER_TEMPLATE.format(
            query=query,
            entities=entities or "无",
            complexity_score=str(complexity_score),
            history=history or "无",
        )
        return f"{sys_prompt}\n\n{user_prompt}"

    def invalidate_cache(self) -> None:
        """清空缓存，下次调用会重新拉取。

        在 Langfuse prompt 版本更新后调用，强制重新获取最新版本。
        """
        self._cache.clear()
        logger.debug("Prompt cache cleared")
