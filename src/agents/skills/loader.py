"""SkillLoader — 扫描 skills/<name>/SKILL.md 并解析 frontmatter + 正文。

SKILL.md 结构：YAML frontmatter（--- 包裹）+ 正文。frontmatter 字段：
name/description/context/model/thinking/allowed-tools/max-iterations（横线键转
下划线）。解析规则：
- name 缺省用目录名；description 缺省用正文首段
- context 非法值回落 inline 并记 warning（fail-open，不阻塞加载）
- 正文按 context 存 inline_prompt（inline）或 agent_prompt（fork）
- 只扫一层 skills/<name>/SKILL.md，不递归（目录即 skill 边界）
"""

import warnings
from pathlib import Path

import yaml

from src.agents.skills.models import SkillContext, SkillRecord

_FRONTMATTER_KEYS = {
    "name": str,
    "description": str,
    "context": str,
    "model": str | None,
    "thinking": bool | None,
    "allowed-tools": list,
    "max-iterations": int | None,
}


class SkillLoader:
    """从 skills_root 扫描并解析全部 SKILL.md 为 SkillRecord。"""

    def __init__(self, skills_root: Path) -> None:
        """初始化加载器。

        Args:
            skills_root: skills 内容库根目录（含 <name>/SKILL.md 子目录）
        """
        self.skills_root = skills_root

    def load_all(self) -> list[SkillRecord]:
        """扫描 skills_root 下全部 skill，解析为 SkillRecord 列表。

        Returns:
            解析成功的 SkillRecord 列表；目录不存在或为空时返回空列表。
            单文件解析失败记 warning 并跳过（fail-open，坏 skill 不拖垮整体）。
        """
        records: list[SkillRecord] = []
        if not self.skills_root.exists():
            return records
        for skill_dir in sorted(self.skills_root.iterdir()):
            if not skill_dir.is_dir():
                continue
            path = skill_dir / "SKILL.md"
            if not path.exists():
                continue
            try:
                records.append(self._parse(path, skill_dir.name))
            except (yaml.YAMLError, ValueError) as exc:
                warnings.warn(f"skill {skill_dir.name} 解析失败，已跳过: {exc}")
        return records

    def _parse(self, path: Path, fallback_name: str) -> SkillRecord:
        """解析单个 SKILL.md。

        Args:
            path: SKILL.md 文件路径
            fallback_name: frontmatter 缺 name 时用的目录名

        Returns:
            SkillRecord（source_path 为 path 绝对路径）

        Raises:
            yaml.YAMLError: frontmatter YAML 无法解析
            ValueError: frontmatter 非 dict / 字段类型不符
        """
        raw = path.read_text(encoding="utf-8")
        meta, body = self._split_frontmatter(raw)
        name = meta.get("name")
        if not isinstance(name, str):
            name = fallback_name
        description = meta.get("description")
        if not isinstance(description, str) or not description:
            description = self._first_paragraph(body)
        context = meta.get("context", SkillContext.INLINE)
        if context not in (SkillContext.INLINE, SkillContext.FORK):
            warnings.warn(f"skill {name} context 非法值 {context!r}，回落 inline")
            context = SkillContext.INLINE
        allowed_tools = meta.get("allowed-tools")
        if not isinstance(allowed_tools, list):
            allowed_tools = []
        model = meta.get("model")
        if not isinstance(model, str):
            model = None
        thinking = meta.get("thinking")
        if not isinstance(thinking, bool):
            thinking = None
        max_iterations = meta.get("max-iterations")
        if isinstance(max_iterations, bool) or not isinstance(max_iterations, int):
            max_iterations = None
        if context == SkillContext.INLINE:
            inline_prompt = body
            agent_prompt = None
        else:
            inline_prompt = None
            agent_prompt = body
        return SkillRecord(
            name=name,
            description=description,
            context=context,
            inline_prompt=inline_prompt,
            agent_prompt=agent_prompt,
            model=model,
            thinking=thinking,
            allowed_tools=[str(t) for t in allowed_tools],
            max_iterations=max_iterations,
            source_path=path.resolve(),
        )

    def _split_frontmatter(self, raw: str) -> tuple[dict, str]:
        """把 SKILL.md 拆成 (frontmatter dict, 正文)。

        Args:
            raw: 文件全文

        Returns:
            (frontmatter dict, 正文 str)；无 frontmatter 时返回 (空 dict, 全文)

        Raises:
            yaml.YAMLError: frontmatter 不是合法 YAML
        """
        lines = raw.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}, raw
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                meta = yaml.safe_load("\n".join(lines[1:i]))
                body = "\n".join(lines[i + 1 :]).strip()
                if meta is None:
                    return {}, body
                if not isinstance(meta, dict):
                    raise ValueError("frontmatter 必须是 mapping")
                return meta, body
        # 没有闭合 ---：按无 frontmatter 处理（正文全文）
        return {}, raw

    def _first_paragraph(self, body: str) -> str:
        """取正文首段作 description 兜底（按空行切分取第一段，截断到 200 字）。"""
        if not body:
            return ""
        paragraph = body.split("\n\n")[0].replace("\n", " ").strip()
        return paragraph[:200]
