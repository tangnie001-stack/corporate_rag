"""P0 闸门（模板层）：12 条模板正文与搬迁前 golden 逐字相同。

设计依据：change prompt-layering-and-domain-binding 的 design.md D5 / D12.6 Q4。
本层是"搬运无损"的强证明：纯文本比对、不触网、不依赖组装逻辑。
迁移后 golden 不得修改（改动 golden 等于把闸门归零）。
"""

import json
from pathlib import Path

import pytest

GOLDEN = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "prompt_golden"
    / "templates.json"
)


@pytest.fixture(scope="module")
def golden() -> dict:
    """读取 golden（迁移前生成，之后不得重生成）。"""
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_golden_has_twelve_templates(golden: dict) -> None:
    """黄金样本覆盖 12 条迁移常量。"""
    assert len(golden) == 12


def test_migrated_templates_match_golden_byte_for_byte(golden: dict) -> None:
    """每条模板的正文与 golden 逐字节相同（搬运无损的正证明）。"""
    # loader 由 T2 包化 + T4 实现；此刻该模块尚不存在，导入即红 —— 这正是本任务的 TDD 先红
    from src.config.prompts import loader  # pyright: ignore[reportAttributeAccessIssue]

    for template_id, entry in golden.items():
        actual = loader.get_content(template_id)
        assert actual == entry["content"], (
            f"模板 {template_id}（常量 {entry['const']}）搬运后与 golden 不一致；"
            f"长度 golden={len(entry['content'])} actual={len(actual)}"
        )
