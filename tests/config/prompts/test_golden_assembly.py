"""P0 闸门（组装层）：固定输入下最终 system 文本与搬迁前 golden 逐字相同。

与模板层闸门的分工（见 plan 的 Task 1）：本层只钉"哪几段、什么顺序、什么分隔"，
base 正文用 sentinel 隔离，故本层不依赖任何具体 prompt 文案。

日期不取挂钟：golden 由 `scripts/gen_prompt_golden.py` 的 FROZEN_DATE 冻结生成，
本测试从同一脚本读取 FROZEN_DATE，并把 `src.rag.prompt._with_current_date` 换成
锚定该常量的替身，故闸门与日历解耦。
"""

import importlib.util
import json
from datetime import date
from pathlib import Path
from typing import Protocol, TypedDict, cast
from unittest.mock import MagicMock

import pytest

from src.infra.llm.prompt_manager import PromptManager
from src.rag.prompt import build_system_prompt

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GOLDEN = _REPO_ROOT / "tests" / "fixtures" / "prompt_golden" / "assembly.json"
_GENERATOR = _REPO_ROOT / "scripts" / "gen_prompt_golden.py"

_BASE_SENTINEL = "<BASE-SENTINEL>"


class _CaseKwargs(TypedDict):
    """组装用例的关键字参数，字段与 build_system_prompt 前三个参数同名同型。"""

    persona: str
    kb_bound: bool
    has_skills: bool


class _GeneratorModule(Protocol):
    """生成器脚本对本测试可见的接口：冻结日期常量。"""

    FROZEN_DATE: str


# 五种输入组合，键名与 golden 的键一一对应
_CASES: dict[str, _CaseKwargs] = {
    "A_unbound_no_persona": {"persona": "", "kb_bound": False, "has_skills": False},
    "B_unbound_with_persona": {
        "persona": "你是财务专家。",
        "kb_bound": False,
        "has_skills": False,
    },
    "C_bound_no_persona": {"persona": "", "kb_bound": True, "has_skills": False},
    "D_bound_with_persona_no_skills": {
        "persona": "你是财务专家。",
        "kb_bound": True,
        "has_skills": False,
    },
    "E_bound_with_persona_and_skills": {
        "persona": "你是财务专家。",
        "kb_bound": True,
        "has_skills": True,
    },
}


def _stub_pm() -> PromptManager:
    """替身 PromptManager：只实现 build_system_prompt 用到的方法，绝不触网（F4）。

    Returns:
        带 get_base_system_prompt / get_user_template 的 MagicMock；
        类型上冒充 PromptManager，避免 pyright 报参数类型不符
    """
    pm = MagicMock()
    pm.get_base_system_prompt.return_value = _BASE_SENTINEL
    pm.get_user_template.return_value = "用户模板"
    return cast(PromptManager, pm)


def _load_generator() -> _GeneratorModule:
    """按文件路径加载生成器脚本，用于读取 FROZEN_DATE（不触及其 main）。

    Returns:
        已执行的 gen_prompt_golden 模块对象，按 _GeneratorModule 协议暴露 FROZEN_DATE
    """
    spec = importlib.util.spec_from_file_location("gen_prompt_golden", _GENERATOR)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载生成器脚本：{_GENERATOR}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_GeneratorModule, module)


@pytest.fixture(scope="module")
def golden() -> dict[str, str]:
    """读取组装层 golden（迁移前生成，不得重生成）。"""
    return json.loads(_GOLDEN.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def frozen_date() -> str:
    """生成器脚本里的 FROZEN_DATE —— 本测试唯一的日期来源。"""
    return _load_generator().FROZEN_DATE


@pytest.fixture
def frozen_date_patch(frozen_date: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """把 `src.rag.prompt._with_current_date` 换成锚定 FROZEN_DATE 的替身。"""

    def _with_frozen_date(prompt: str) -> str:
        """`_with_current_date` 的替身：格式与幂等守卫一致，日期固定为 FROZEN_DATE。

        Args:
            prompt: 原始系统提示词文本

        Returns:
            追加冻结日期行后的提示词文本
        """
        frozen = date.fromisoformat(frozen_date)
        date_line = f"\n今天是 {frozen.year}年{frozen.month}月{frozen.day}日。\n"
        if date_line.strip() in prompt:
            return prompt
        return prompt + date_line

    # build_system_prompt 以模块全局名解析日期函数，故替换 module 属性即可生效
    monkeypatch.setattr("src.rag.prompt._with_current_date", _with_frozen_date)


@pytest.mark.parametrize("case_name", list(_CASES))
def test_assembly_matches_golden(
    case_name: str,
    golden: dict[str, str],
    frozen_date_patch: None,
) -> None:
    """五种组合的最终 system 文本逐字相同（多条消息以换行 + `---` 分隔）。"""
    messages = build_system_prompt(prompt_manager=_stub_pm(), **_CASES[case_name])
    actual = "\n---\n".join(cast(str, m.content) for m in messages)
    assert actual == golden[case_name], f"组装层不一致：case={case_name}"
