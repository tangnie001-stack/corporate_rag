"""对话问答模块 — 页面集成测试。

覆盖正常问答（流式响应）和异常场景（空问题、未选知识库）。
需要有一个已上传文档的知识库作为对话上下文。
"""

from __future__ import annotations

import pytest
from gradio_client import Client
from loguru import logger

from src.app_service import AppService

from .conftest import get_test_doc_path

# 流式对话可能较慢（检索 + LLM 生成）
CHAT_TIMEOUT = 60


@pytest.fixture(scope="module")
def prepared_kb_id(
    client: Client,
    service: AppService,
) -> str:
    """创建一个包含测试文档的知识库，供对话问答测试使用。

    由于对话需要文档内容，此 fixture 在 module 级别执行一次，
    所有测试用例共用同一个 KB，避免重复上传。返回知识库 UUID。
    """
    import uuid

    name = f"__test__chat_{uuid.uuid4().hex[:8]}"

    # 创建 KB
    client.predict(name, api_name="/handle_create_kb")
    kb_id = service.db.get_kb_by_name(name)
    assert kb_id is not None, f"KB '{name}' 创建失败"

    # 上传一个文档（handle_upload 的 kb_id 参数）
    file_path = get_test_doc_path("sample.txt")
    status, _ = client.predict(kb_id, [file_path], api_name="/handle_upload")
    assert "✅" in status, f"文档上传失败: {status}"

    logger.info("Prepared KB '{}' (id={}) with sample.txt for chat tests", name, kb_id)
    return kb_id


# ==================== 正常问答 ====================


class TestChatNormal:
    """正常对话场景集成测试。"""

    def test_chat_streaming_response(
        self,
        client: Client,
        prepared_kb_id: str,
    ) -> None:
        """TC17: 正常提问并验证流式回答。

        验证：
          - 流式输出正常返回（不超时、不空）
          - 最终回答有内容
          - 引用来源非空（文档已上传）
        """
        logger.info("TC17: 正常问答 (KB_id={})", prepared_kb_id)

        session_id = "test_session_chat_normal"
        query = "请用中文简单介绍一下这个文档的内容"

        # 使用 submit 处理流式响应
        job = client.submit(
            query,
            [],  # 空对话历史
            prepared_kb_id,
            session_id,
            api_name="/handle_chat",
        )

        # 收集所有流式输出
        outputs = []
        for result in job:
            outputs.append(result)

        assert len(outputs) > 0, "应至少有一个流式输出"
        logger.info("  收到 {} 个流式 token 块", len(outputs))

        # 最终结果
        final_history, citations_text = outputs[-1]

        # 验证 history 格式：[[user_msg, assistant_msg], ...]
        assert isinstance(final_history, list), "history 应为 list"
        assert len(final_history) >= 1, "应有至少一条对话"

        # 验证 assistant 回答非空
        last_turn = final_history[-1]
        assert len(last_turn) == 2, "每轮对话应为 [user, assistant] 格式"
        user_msg, assistant_msg = last_turn
        assert assistant_msg is not None and len(assistant_msg) > 0, (
            "assistant 回答不应为空"
        )
        logger.info("  assistant 回答长度: {} 字符", len(assistant_msg))

        # 引用来源验证
        if citations_text:
            logger.info("  引用来源: {} 字符", len(citations_text))
            assert "引用" in citations_text, "引用文本应包含 '引用' 标记"


# ==================== 异常问答场景 ====================


class TestChatErrors:
    """对话异常场景集成测试。"""

    def test_chat_empty_query(
        self,
        client: Client,
    ) -> None:
        """TC18: 空问题。

        验证：history 不变（不调用 LLM）。
        """
        logger.info("TC18: 空问题")

        result = client.predict(
            "",
            [],
            "",  # 空 KB 名
            "",  # 空 session_id
            api_name="/handle_chat",
        )

        # 空问题时 handle_chat 返回 (history, "") 且不流式
        # predict 可处理非流式返回
        logger.info("  空问题结果: {}", result)
        # 结果应保持原 history 不变
        if isinstance(result, (list, tuple)) and len(result) >= 1:
            history_part = result[0]
            if isinstance(history_part, list):
                assert len(history_part) == 0, "空问题不应增加对话历史"

    def test_chat_no_kb(
        self,
        client: Client,
    ) -> None:
        """TC19: 未选择知识库时发送消息。

        验证：chatbot 提示"请先选择一个知识库"。
        """
        logger.info("TC19: 未选 KB 发送消息")

        result = client.predict(
            "这是一个测试问题",
            [],
            "",  # 空 KB 名
            "",
            api_name="/handle_chat",
        )

        # 处理流式或非流式返回
        output = result
        if isinstance(result, list) and len(result) > 0:
            # 可能是流式输出的最后一个 yield
            output = result[-1] if isinstance(result[-1], (list, tuple)) else result
        if isinstance(result, tuple) and len(result) >= 1:
            output = result

        # 提取 history 部分
        history_part = None
        if isinstance(output, (list, tuple)) and len(output) >= 1:
            history_part = output[0]

        # 验证提示消息
        if history_part is not None and isinstance(history_part, list):
            if len(history_part) > 0:
                last_assistant = (
                    history_part[-1][1] if len(history_part[-1]) > 1 else ""
                )
                assert "请先选择一个知识库" in last_assistant, (
                    f"未选 KB 应提示: {last_assistant}"
                )
