"""reasoning_content 请求侧回传测试（design D4）——覆写点为 _get_request_payload。"""

from langchain_core.messages import AIMessage, HumanMessage

from src.infra.llm.reasoning_chat import ReasoningPreservingChatQwen


def _llm() -> ReasoningPreservingChatQwen:
    return ReasoningPreservingChatQwen(model="qwen3.7-flash", api_key="test")


class TestReasoningPreserving:
    def test_assistant_reasoning_injected_into_payload(self):
        messages = [
            HumanMessage(content="计算腾讯2025年毛利率"),
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "retrieve_kb", "args": {"query": "q"}, "id": "t1"}
                ],
                additional_kwargs={"reasoning_content": "先检索知识库"},
            ),
        ]
        payload = _llm()._get_request_payload(messages)
        assistant = next(m for m in payload["messages"] if m["role"] == "assistant")
        assert assistant["reasoning_content"] == "先检索知识库"
        assert assistant["tool_calls"][0]["function"]["name"] == "retrieve_kb"

    def test_no_reasoning_no_field(self):
        messages = [AIMessage(content="hi")]
        payload = _llm()._get_request_payload(messages)
        assert "reasoning_content" not in payload["messages"][0]

    def test_extra_body_preserve_thinking(self):
        extra_body = _llm().extra_body
        assert extra_body is not None
        assert extra_body.get("preserve_thinking") is True
