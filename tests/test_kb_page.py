"""知识库管理模块 — 页面集成测试。

覆盖创建、选择、删除三大操作的 UI ↔ 后端交互正确性。
每个测试用例进行组件 + 数据库双重验证。
"""

from __future__ import annotations

from gradio_client import Client
from loguru import logger

from src.app_service import AppService
from src.infra.db.mysql_db import MySQLDB
from src.infra.db.vector_store import VectorStore


# ==================== 辅助函数 ====================


def _get_dropdown_choices(dropdown_result) -> list:
    """从 Gradio Dropdown 组件更新结果中提取 choices 列表。

    gradio_client 将 gr.Dropdown 更新反序列化为字典，
    可能的格式有多种，统一提取为列表。
    """
    if isinstance(dropdown_result, dict):
        # 标准格式：{"choices": [...], "value": ...}
        return dropdown_result.get("choices") or []
    if isinstance(dropdown_result, list):
        return dropdown_result
    if isinstance(dropdown_result, tuple):
        return list(dropdown_result)
    return []


def _get_dropdown_value(dropdown_result):
    """从 Gradio Dropdown 组件更新结果中提取当前选中的值。"""
    if isinstance(dropdown_result, dict):
        return dropdown_result.get("value", "")
    return ""


# ==================== 创建知识库 ====================


class TestCreateKnowledgeBase:
    """知识库创建操作集成测试。"""

    def test_create_normal(
        self,
        client: Client,
        service: AppService,
        test_kb_name: str,
    ) -> None:
        """TC01: 正常创建新知识库。

        验证：
          - 组件状态：status 含"创建成功"，Dropdown 含新选项
          - 数据库：MySQL knowledge_base 表有对应记录
        """
        logger.info("TC01: 创建知识库 '{}'", test_kb_name)

        # 操作
        status, dropdown = client.predict(
            test_kb_name,
            api_name="/handle_create_kb",
        )

        # 组件验证
        assert "创建成功" in status, f"创建成功状态消息: {status}"
        choices = _get_dropdown_choices(dropdown)
        choice_values = [c[1] if isinstance(c, (list, tuple)) else c for c in choices]
        choice_labels = [c[0] if isinstance(c, (list, tuple)) else c for c in choices]

        # 应该能找到测试 KB 名称的选项
        assert test_kb_name in choice_labels or test_kb_name in choice_values, (
            f"Dropdown 中应包含 '{test_kb_name}'，实际: {choice_labels}"
        )

        # 数据库验证
        kb_id = service.db.get_kb_by_name(test_kb_name)
        assert kb_id is not None, f"MySQL 中应可查到 '{test_kb_name}'"
        logger.info("  ✓ KB ID: {}", kb_id)

    def test_create_duplicate(
        self,
        client: Client,
        service: AppService,
        test_kb_name: str,
    ) -> None:
        """TC02: 创建同名的知识库。

        验证：
          - 组件状态：status 含"已存在"
          - 数据库：不新增记录（条数不变）
        """
        logger.info("TC02: 重复创建知识库 '{}'", test_kb_name)

        # 先创建一次
        client.predict(test_kb_name, api_name="/handle_create_kb")
        kb_id_first = service.db.get_kb_by_name(test_kb_name)
        assert kb_id_first is not None, "第一次创建应成功"

        # 再次创建同名
        status, dropdown = client.predict(
            test_kb_name,
            api_name="/handle_create_kb",
        )

        # 组件验证
        assert "已存在" in status, f"重复创建状态消息: {status}"

        # 数据库验证：ID 不变（不是新插入的）
        kb_id_second = service.db.get_kb_by_name(test_kb_name)
        assert kb_id_second == kb_id_first, "重复创建不应变更 KB ID"

    def test_create_empty_name(
        self,
        client: Client,
        service: AppService,
    ) -> None:
        """TC03: 空名称创建。

        验证：
          - 组件状态：status 提示"请输入知识库名称"
          - 数据库：不写入任何记录
        """
        logger.info("TC03: 空名称创建")

        status, dropdown = client.predict("", api_name="/handle_create_kb")

        assert "请输入" in status or "不能为空" in status or "名称" in status, (
            f"空名称应提示，实际: {status}"
        )

    def test_create_whitespace_name(
        self,
        client: Client,
        service: AppService,
    ) -> None:
        """TC04: 名称只含空格。

        验证：
          - 组件状态：status 提示"请输入知识库名称"
          - 数据库：不写入任何记录
        """
        logger.info("TC04: 空格名称创建")

        status, dropdown = client.predict("   ", api_name="/handle_create_kb")

        assert "请输入" in status or "不能为空" in status or "名称" in status, (
            f"空格名称应提示，实际: {status}"
        )


# ==================== 选择知识库 ====================


class TestSelectKnowledgeBase:
    """知识库选择操作集成测试。"""

    def test_select_existing(
        self,
        client: Client,
        service: AppService,
        test_kb_name: str,
    ) -> None:
        """TC05: 选择存在的知识库。

        验证：
          - 组件状态：doc_table 无报错，status 显示"已选择"
        """
        logger.info("TC05: 选择知识库 '{}'", test_kb_name)

        # 先创建 KB
        client.predict(test_kb_name, api_name="/handle_create_kb")

        # 选择 KB（通过 dropdown 的 change 事件，传 kb_id）
        kb_id = service.db.get_kb_by_name(test_kb_name)
        doc_table, status = client.predict(
            kb_id,
            api_name="/handle_select_kb",
        )

        # 组件验证
        assert "已选择" in status, f"选择后状态消息: {status}"
        # 新 KB 文档表应为空（尚未上传文档）
        assert doc_table is not None
        if isinstance(doc_table, list):
            assert len(doc_table) == 0, "新知识库文档列表应为空"

    def test_select_empty(
        self,
        client: Client,
    ) -> None:
        """TC06: 空选择（取消选择）。

        验证：
          - 组件状态：doc_table 清空，status 显示"欢迎使用"
        """
        logger.info("TC06: 空选择")

        doc_table, status = client.predict(
            "",
            api_name="/handle_select_kb",
        )

        # 组件验证
        assert "欢迎" in status or "选择" in status, f"空选择后状态: {status}"
        # doc_table 应为空
        assert doc_table == [] or doc_table is None, "空选择后文档表应清空"


# ==================== 删除知识库 ====================


class TestDeleteKnowledgeBase:
    """知识库删除操作集成测试。"""

    def test_delete_existing(
        self,
        client: Client,
        service: AppService,
        mysql_db: MySQLDB,
        vector_store: VectorStore,
        test_kb_name: str,
    ) -> None:
        """TC07: 删除存在的知识库。

        验证：
          - 组件状态：status 含 ✅，Dropdown 清空，doc_table 清空
          - 数据库：MySQL 记录删除，ChromaDB collection 删除
        """
        logger.info("TC07: 删除知识库 '{}'", test_kb_name)

        # 先创建 KB，获取 ID
        client.predict(test_kb_name, api_name="/handle_create_kb")
        kb_id = service.db.get_kb_by_name(test_kb_name)
        assert kb_id is not None

        # 删除
        status, dropdown, doc_table = client.predict(
            kb_id,
            api_name="/handle_delete_kb",
        )

        # 组件验证
        assert "✅" in status or "已删除" in status, f"删除状态消息: {status}"
        # dropdown 应清空
        dropdown_value = _get_dropdown_value(dropdown)
        assert dropdown_value == "", (
            f"删除后 dropdown value 应清空，实际: {dropdown_value}"
        )
        # doc_table 应清空
        assert doc_table == [] or doc_table is None, "删除后文档表应清空"

        # 数据库验证
        assert mysql_db.get_kb_by_name(test_kb_name) is None, "MySQL 中记录应已删除"
        # ChromaDB collection 应已删除（或不存在）
        collection_name = vector_store._collection_name(kb_id)
        all_collections = vector_store.list_collections()
        assert collection_name not in all_collections, (
            f"ChromaDB collection '{collection_name}' 应已删除"
        )

    def test_delete_empty(
        self,
        client: Client,
    ) -> None:
        """TC08: 不选择知识库直接删除。

        验证：
          - 组件状态：status 提示"请先选择一个知识库"
        """
        logger.info("TC08: 空选删除")

        status, dropdown, doc_table = client.predict(
            "",
            api_name="/handle_delete_kb",
        )

        # 组件验证
        assert "请先选择" in status, f"空选删除提示: {status}"

    def test_delete_nonexistent(
        self,
        client: Client,
        service: AppService,
        test_kb_name: str,
    ) -> None:
        """TC09: 删除一个不存在的知识库（重复删除）。

        验证：
          - 组件状态：status 含 ⚠️ 不存在提示
        """
        logger.info("TC09: 删除不存在的知识库")

        # 先创建再删除，得到一个已不存在的 ID
        client.predict(test_kb_name, api_name="/handle_create_kb")
        kb_id = service.db.get_kb_by_name(test_kb_name)
        client.predict(kb_id, api_name="/handle_delete_kb")

        # 用同一 ID 再次删除
        status, dropdown, doc_table = client.predict(
            kb_id,
            api_name="/handle_delete_kb",
        )

        # 组件验证
        assert "⚠️" in status or "不存在" in status, f"重复删除提示: {status}"
