"""Prompt 模板的门面 — 按 id 取正文并渲染占位符，唯一读取路径是加载入口。

使用方式：
    loader = PromptManager()
    base = loader.get_base_system_prompt(domain="finance")
    user_tmpl = loader.get_user_template(context=context, query=query)

远端名单（PROMPT_NAMES）已出列，本地 YAML 模板是唯一事实源；`_fetch_prompt` /
`_get` / 缓存实现保留，名单为空时不发起网络请求，直接返回本地正文。
"""

import json
import time
from datetime import datetime
from typing import ClassVar
from urllib.error import URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from loguru import logger

from src.config.prompts import loader
from src.core import logging as core_logging
from src.core.log_events import Event

# 北京时区：金融场景锚定"本报告期/今年"需按北京时间取日期。
# 若用 UTC，北京 00:00-07:59 之间日期落后一天，月初/年初清晨会锚定错"今年/去年"。
_BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def _with_current_date(prompt: str) -> str:
    """在系统提示词末尾追加今日日期，锚定相对时间表达（本报告期/今年）。

    重复调用时若日期行已存在则直接返回，保证幂等。
    日期在段组装层追加而非存入缓存，避免 _get() 60s 缓存跨天返回旧日期。
    日期按北京时间（Asia/Shanghai）计算，避免 UTC 在凌晨时段日期落后一天。

    Args:
        prompt: 原始系统提示词文本

    Returns:
        追加今日日期行后的提示词文本
    """
    today = datetime.now(_BEIJING_TZ).date()
    date_line = f"\n今天是 {today.year}年{today.month}月{today.day}日。\n"
    if date_line.strip() in prompt:
        return prompt
    return prompt + date_line


class PromptManager:
    """prompt 模板的门面 —— 唯一读取路径是 `src/config/prompts/loader`。

    本类不持有任何模板正文副本，也不参与段组装（组装在 `src/rag/prompt.py`）；
    它的职责只有"按 id 取正文 + 渲染占位符"，与加载入口是转发关系而非第二事实源。

    远端名单已出列（见 `docs/adr/0010-delist-langfuse-prompts.md`）：本地模板是唯一
    事实源。拉取实现（`_fetch_prompt` / `_get` / 缓存）保留，使终态接入只需"加回名单
    + 固定 label/版本"，而 `_resolve` 在名单为空时直接返回本地正文、不发起网络请求。

    Args:
        cache_ttl: 远端缓存有效期（秒），仅在名单非空时生效，默认 60
    """

    PROMPT_NAMES: ClassVar[dict[str, str]] = {}

    def __init__(self, cache_ttl: int = 60) -> None:
        """从环境变量读取 Langfuse 配置。

        Args:
            cache_ttl: 远端缓存有效期（秒），默认 60 秒
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
                core_logging.log_event(
                    Event.PROMPT_FETCHED, name=name, version=data.get("version")
                )
            return prompt_text
        except URLError as e:
            core_logging.log_event(Event.PROMPT_FETCH_FAILED, name=name, err=str(e))
        except (json.JSONDecodeError, KeyError) as e:
            core_logging.log_event(Event.PROMPT_FETCH_FAILED, name=name, err=str(e))
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
        core_logging.log_event(Event.PROMPT_FALLBACK, name=name)
        self._cache[name] = (fallback, now + self._cache_ttl)
        return fallback

    def _resolve(self, key: str, local: str) -> str:
        """远端名单有该键则按名单取（失败兜底 local），否则直接用 local。

        Args:
            key: PROMPT_NAMES 的键（system / user / classifier）
            local: 本地模板正文（本期的唯一事实源）

        Returns:
            prompt 文本
        """
        name = self.PROMPT_NAMES.get(key)
        if not name:
            return local
        return self._get(name, local)

    def get_base_system_prompt(self, domain: str = "general") -> str:
        """取指定领域的 base 段正文（人设层的默认来源）。

        不做引用指令 / 委派引导 / 日期追加 —— 那些属环境约束层，由
        `src/rag/prompt.build_system_prompt` 统一处理（保证唯一入口）。

        Args:
            domain: 领域名；缺省保留值 general

        Returns:
            该领域的 base 正文
        """
        return self._resolve("system", loader.get_domain_base(domain))

    def get_user_template(self, context: str = "", query: str = "") -> str:
        """渲染用户消息模板。

        Args:
            context: 检索到的文档上下文文本
            query: 用户查询文本

        Returns:
            渲染后的用户消息文本（未提供的占位符原样保留）
        """
        template = self._resolve("user", loader.get_content("task-user-prompt"))
        return loader.render(template, {"context": context, "query": query})

    def get_classifier_prompt(
        self,
        query: str,
        entities: str,
        complexity_score: float,
        history: str,
        kb_entities: str = "",
    ) -> str:
        """渲染分类器 prompt（系统提示 + 用户消息）。

        Args:
            query: 用户原始查询文本
            entities: 已提取实体列表（字符串）
            complexity_score: 规则预判的复杂度评分
            history: 最近对话历史文本
            kb_entities: KB 聚合的候选实体（公司/报告期/代码），默认空串兜底为"无"

        Returns:
            完整的分类器 prompt 文本
        """
        sys_prompt = self._resolve(
            "classifier", loader.get_content("task-classifier-system")
        )
        user_prompt = loader.render(
            loader.get_content("task-classifier-user"),
            {
                "query": query,
                "entities": entities or "无",
                "kb_entities": kb_entities or "无",
                "complexity_score": str(complexity_score),
                "history": history or "无",
            },
        )
        return f"{sys_prompt}\n\n{user_prompt}"

    def invalidate_cache(self) -> None:
        """清空缓存，下次调用会重新拉取。

        在 Langfuse prompt 版本更新后调用，强制重新获取最新版本。
        """
        self._cache.clear()
        logger.debug("Prompt cache cleared")
