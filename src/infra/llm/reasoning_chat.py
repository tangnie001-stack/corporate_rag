"""ChatOpenAI 子类：保留第三方模型流式 chunk 的 reasoning_content 思考文本。

langchain-openai 1.3.3 只解析官方 OpenAI 字段，第三方（DashScope 等）的
reasoning_content 被丢弃。本子类重写 _convert_chunk_to_generation_chunk，
把 delta 里的 reasoning_content（fallback reasoning）累积到
AIMessageChunk.additional_kwargs["reasoning_content"]，供上层读取。

参考：langchain-ai/langchain issue #38764 的 ReasoningChatOpenAI 实现。
"""

from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI


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
