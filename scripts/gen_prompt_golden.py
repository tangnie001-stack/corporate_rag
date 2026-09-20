"""一次性生成 P0 的 prompt golden（迁移前运行，之后不得重跑覆盖）。

用法：python -m scripts.gen_prompt_golden
产物：tests/fixtures/prompt_golden/{templates,assembly}.json

设计要点（见 change 的 design.md D5 / D12.6 Q4）：
- golden 必须从**迁移前的常量路径**产出，故本脚本在 P0 落地后即失效（常量已删）。
- 一律用替身 PromptManager，不构造真实实例，避免走 Langfuse 远端（既有测试
  test_prompt_layers.py:24-33 就是因为构造真实实例才会发网络请求）。

assembly.json 的日期行由 FROZEN_DATE 常量注入：生成时把 `src.rag.prompt._with_current_date`
替换为 `_with_frozen_date`（逐字复刻源实现的格式与幂等守卫，仅日期值不同），使 golden 与日历
解耦、可重复生成。消费方比对时必须锚定同一常量，否则闸门会随日期变红。
"""

import json
from datetime import date
from pathlib import Path
from typing import TypedDict, cast
from unittest.mock import patch

from src.agents.graph.verify import guardrails  # noqa: F401  (仅为断言其可导入)
from src.config import prompts as P
from src.infra.llm.prompt_manager import PromptManager
from src.rag.prompt import build_system_prompt

# 冻结日期：golden 的职责是钉"哪几段、什么顺序、什么分隔"，日期是噪声。
# 生成与断言两侧必须用同一个常量，否则闸门会随日历变红。
FROZEN_DATE: str = "2026-01-01"

# 常量名 → 模板 id（与 plan 的映射表一致）
TEMPLATE_MAP: dict[str, str] = {
    "DELEGATE_GUIDANCE_SECTION": "tools-delegate-guidance",
    "FINANCIAL_SYSTEM_PROMPT": "base-financial",
    "KB_UNBOUND_SYSTEM_PROMPT": "sources-kb-unbound",
    "KB_BOUND_RETRIEVAL_DISCIPLINE": "sources-kb-bound-discipline",
    "USER_PROMPT_TEMPLATE": "task-user-prompt",
    "CLASSIFIER_SYSTEM_PROMPT": "task-classifier-system",
    "CLASSIFIER_USER_TEMPLATE": "task-classifier-user",
    "REWRITE_SYSTEM_PROMPT": "task-rewrite-system",
    "REWRITE_USER_TEMPLATE": "task-rewrite-user",
    "ENTITY_EXTRACTION_SYSTEM_PROMPT": "task-entity-system",
    "ENTITY_EXTRACTION_USER_TEMPLATE": "task-entity-user",
    "INLINE_CITATION_INSTRUCTION": "output-inline-citation",
}


# 拼接前的基础段：FINANCIAL_SYSTEM_PROMPT 末尾拼了 DELEGATE_GUIDANCE_SECTION（F2），
# golden 存"拼接前"的部分，模板也只存这一部分。
def _financial_base() -> str:
    """返回 FINANCIAL_SYSTEM_PROMPT 去掉尾部 DELEGATE_GUIDANCE_SECTION 的正文。"""
    full = P.FINANCIAL_SYSTEM_PROMPT
    tail = P.DELEGATE_GUIDANCE_SECTION
    if not full.endswith(tail):
        raise AssertionError(
            "FINANCIAL_SYSTEM_PROMPT 尾部不是 DELEGATE_GUIDANCE_SECTION，"
            "拼接假设已变，需重检 plan 的 F2"
        )
    return full[: -len(tail)]


class _StubPromptManager:
    """替身：只实现 build_system_prompt 用到的方法，绝不触网（F4）。"""

    BASE_SENTINEL = "<BASE-SENTINEL>"

    def get_base_system_prompt(self) -> str:
        """返回 base 段哨兵值，使组装层 golden 与具体正文解耦。"""
        return self.BASE_SENTINEL

    def get_user_template(self, context: str = "", query: str = "") -> str:
        """用本地用户模板渲染占位符，签名与 PromptManager 同名方法一致。"""
        return P.USER_PROMPT_TEMPLATE.format(context=context, query=query)


class _CaseKwargs(TypedDict):
    """组装用例的关键字参数，字段与 build_system_prompt 前三个参数同名同型。"""

    persona: str
    kb_bound: bool
    has_skills: bool


def _templates() -> dict[str, dict[str, str]]:
    """按 TEMPLATE_MAP 抽取 12 条模板正文，返回 `模板id → {const, content}`。"""
    result: dict[str, dict[str, str]] = {}
    for const_name, tid in TEMPLATE_MAP.items():
        if const_name == "FINANCIAL_SYSTEM_PROMPT":
            content = _financial_base()
        else:
            content = getattr(P, const_name)
        result[tid] = {"const": const_name, "content": content}
    return result


def _with_frozen_date(prompt: str) -> str:
    """`_with_current_date` 的替身：格式与幂等守卫逐字一致，日期固定为 FROZEN_DATE。

    Args:
        prompt: 原始系统提示词文本

    Returns:
        追加冻结日期行后的提示词文本
    """
    frozen = date.fromisoformat(FROZEN_DATE)
    date_line = f"\n今天是 {frozen.year}年{frozen.month}月{frozen.day}日。\n"
    if date_line.strip() in prompt:
        return prompt
    return prompt + date_line


def _assembly() -> dict[str, str]:
    """组装层 golden：固定输入下的最终 system 文本（分离 base 正文，只钉组装行为）。

    用 sentinel 作 base，使本层只证明"哪几段、什么顺序、什么分隔"，与正文内容解耦 ——
    正文由 templates.json 那一层钉死。

    Returns:
        `用例名 → system 消息按序拼接结果`（消息间以 `\\n---\\n` 分隔）
    """
    pm = _StubPromptManager()
    cases: dict[str, _CaseKwargs] = {
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
    out: dict[str, str] = {}
    for name, kwargs in cases.items():
        messages = build_system_prompt(
            prompt_manager=cast(PromptManager, pm),
            **kwargs,
        )
        # 多条 system 消息按序拼接后用 \n---\n 分隔，保留"几条消息"这一结构信息
        out[name] = "\n---\n".join(cast(str, m.content) for m in messages)
    return out


def main() -> None:
    """把模板层与组装层 golden 写入 tests/fixtures/prompt_golden/。"""
    root = Path(__file__).resolve().parents[1]
    target = root / "tests" / "fixtures" / "prompt_golden"
    target.mkdir(parents=True, exist_ok=True)
    (target / "templates.json").write_text(
        json.dumps(_templates(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    # build_system_prompt 以模块全局名解析日期函数，故替换 src.rag.prompt 上的绑定即可生效
    with patch("src.rag.prompt._with_current_date", _with_frozen_date):
        assembly = _assembly()
    (target / "assembly.json").write_text(
        json.dumps(assembly, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"golden written to {target}")


if __name__ == "__main__":
    main()
