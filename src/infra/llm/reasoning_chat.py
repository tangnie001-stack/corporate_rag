"""ChatOpenAI 子类：保留第三方模型流式 chunk 的 reasoning_content 思考文本。

langchain-openai 1.3.3 只解析官方 OpenAI 字段，第三方（DashScope 等）的
reasoning_content 被丢弃。本子类重写 _convert_chunk_to_generation_chunk，
把 delta 里的 reasoning_content（fallback reasoning）累积到
AIMessageChunk.additional_kwargs["reasoning_content"]，供上层读取。

参考：langchain-ai/langchain issue #38764 的 ReasoningChatOpenAI 实现。
"""

from typing import Any

from langchain_core.language_models.base import LanguageModelInput
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI
from loguru import logger


class ChatQwenWithReasoning(ChatOpenAI):
    """保留 reasoning_content / reasoning 字段的 ChatOpenAI 子类。"""

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> ChatGenerationChunk | None:
        """转换流式 chunk 为生成块，并提取 reasoning 文本到 additional_kwargs。

        Args:
            chunk: openai SDK chunk 的 dict（含 choices[0].delta）
            default_chunk_class: 默认消息块类型
            base_generation_info: 基础生成信息

        Returns:
            ChatGenerationChunk：正常解析结果；无法转换时返回 None
        """
        # 父类原生解析（content / tool_calls / usage 等）
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if generation_chunk is None:
            return None

        # 兼容两种外层 chunk 结构（普通流式 / beta stream）
        choices = chunk.get("choices", [])
        if not choices and chunk.get("chunk"):
            choices = chunk["chunk"].get("choices", [])
        if not choices:
            return generation_chunk

        delta = choices[0].get("delta", {}) or {}
        reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""

        if reasoning and isinstance(generation_chunk.message, AIMessageChunk):
            prev = generation_chunk.message.additional_kwargs.get(
                "reasoning_content", ""
            )
            generation_chunk.message.additional_kwargs["reasoning_content"] = (
                prev + reasoning
            )
        return generation_chunk


class ReasoningPreservingChatQwen(ChatQwenWithReasoning):
    """在 chunk 侧 reasoning 提取之上，增加请求侧 reasoning_content 回传。

    DashScope 要求：深度思考模式多轮工具调用时，assistant 消息须携带
    reasoning_content（省略降低工具调用准确性），请求须启用 preserve_thinking。
    覆写点为 _get_request_payload（实例方法，langchain-openai 1.3.3 的
    _convert_message_to_dict 是模块级函数不可覆写）。
    """

    def __init__(self, **kwargs) -> None:
        """构造时按需向 extra_body 注入 preserve_thinking=True（不覆盖用户显式配置）。

        仅当请求未显式关闭思考（extra_body.enable_thinking 不为 False）时注入：
        enable_thinking=False 的调用方（fork 子代理、RAGAS 选手/裁判等）请求体
        保持原样，避免 400 或评估基线漂移。

        Args:
            **kwargs: 透传给 ChatQwenWithReasoning 的构造参数
        """
        extra = dict(kwargs.pop("extra_body", None) or {})
        if extra.get("enable_thinking") is not False:
            extra.setdefault("preserve_thinking", True)
        kwargs["extra_body"] = extra
        super().__init__(**kwargs)

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """构建请求 payload，并把 assistant 消息的 reasoning_content 注入其中。

        Args:
            input_: 模型输入（消息列表或 LangChain 支持的其他输入形式）
            stop: 停止词序列
            **kwargs: 透传给父类的额外请求参数

        Returns:
            dict：openai 兼容请求 payload，assistant 消息按需携带 reasoning_content
        """
        messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        payload_messages = payload.get("messages", [])
        if len(messages) != len(payload_messages):
            # 数量不一致说明两侧消息形态分歧，无法一一对应，跳过注入保证请求安全
            logger.warning(
                "reasoning_content injection skipped: message count mismatch "
                "({} vs {})",
                len(messages),
                len(payload_messages),
            )
            return payload
        for msg, msg_dict in zip(messages, payload_messages):
            if msg_dict.get("role") != "assistant":
                continue
            reasoning = msg.additional_kwargs.get("reasoning_content", "")
            if reasoning and not msg_dict.get("reasoning_content"):
                msg_dict["reasoning_content"] = reasoning
        return payload
