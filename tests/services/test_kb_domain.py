"""知识库领域的写入校验与读取（spec <prompt-composition>「知识库领域绑定」）。"""

import pytest

from src.config.prompts import loader
from src.config.response_codes import Code
from src.services.kb_service import KBService
from src.utils.errors import BusinessError


class _FakeKbRepo:
    """最小 KbRepo 替身：只实现 KBService 用到的四个方法。"""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    async def get_or_create_kb(
        self, user_id: str, name: str, description: str = "", domain: str = "general"
    ) -> tuple[str, bool]:
        """按 (user_id, name) 取或建。"""
        for kb_id, row in self.rows.items():
            if row["user_id"] == user_id and row["name"] == name:
                return kb_id, False
        kb_id = f"kb-{len(self.rows)}"
        self.rows[kb_id] = {
            "user_id": user_id,
            "name": name,
            "description": description,
            "domain": domain,
        }
        return kb_id, True

    async def get_kb_domain(self, kb_id: str) -> str:
        """取领域；库不存在回落 general。"""
        row = self.rows.get(kb_id)
        if row is None:
            return "general"
        return str(row["domain"])

    async def update_kb_domain(self, kb_id: str, domain: str) -> bool:
        """写领域；库不存在返回 False。"""
        if kb_id not in self.rows:
            return False
        self.rows[kb_id]["domain"] = domain
        return True


@pytest.mark.asyncio
async def test_create_kb_rejects_unknown_domain() -> None:
    """领域标识不满足判据时在写入前被拒绝（不是读取时静默回退）。"""
    svc = KBService(_FakeKbRepo())  # type: ignore[arg-type]
    with pytest.raises(BusinessError) as exc:
        await svc.create_knowledge_base("人事库", domain="hr")
    assert exc.value.code == Code.VALIDATION_ERROR


@pytest.mark.asyncio
async def test_create_kb_accepts_known_domain_and_default() -> None:
    """已知领域与默认值 general 均通过（默认值自身合法）。"""
    repo = _FakeKbRepo()
    svc = KBService(repo)  # type: ignore[arg-type]

    finance_id, _ = await svc.create_knowledge_base("财务库", domain="finance")
    assert await repo.get_kb_domain(finance_id) == "finance"

    default_id, _ = await svc.create_knowledge_base("默认库")
    assert await repo.get_kb_domain(default_id) == "general"


@pytest.mark.asyncio
async def test_set_domain_validates_before_write() -> None:
    """更新领域同样先校验；非法值不落库。"""
    repo = _FakeKbRepo()
    svc = KBService(repo)  # type: ignore[arg-type]
    kb_id, _ = await svc.create_knowledge_base("财务库", domain="finance")

    with pytest.raises(BusinessError):
        await svc.set_domain(kb_id, "hr")
    assert await repo.get_kb_domain(kb_id) == "finance", "非法值不得落库"

    assert await svc.set_domain(kb_id, "general") is True
    assert await repo.get_kb_domain(kb_id) == "general"


def test_domain_judgement_is_template_existence() -> None:
    """领域识别判据 = 是否存在对应的 base 模板（不是 id 字符串）。"""
    assert loader.has_domain("finance") is True
    assert loader.has_domain("general") is True
    assert loader.has_domain("hr") is False
