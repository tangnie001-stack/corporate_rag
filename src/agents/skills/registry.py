"""SkillRegistry — 聚合 SkillRecord 并按名索引（含懒重载）。

进程内注册表：SkillLoader 加载的 SkillRecord 统一收口。懒重载语义：
delegate_task 调用前（Task 6）先 reload_if_changed() —— 记录当前扫描 signature
（含目录列表与每个 SKILL.md 的 mtime），变化才重扫，避免每次 delegate 全量解析。

删除安全（design D17）：skill 被删 → reload 后 get() 返回 None → delegate_task
返回"skill 不存在"，主 agent 降级自己答。
"""

from __future__ import annotations

import hashlib

from src.agents.skills.loader import SkillLoader
from src.agents.skills.models import SkillRecord


class SkillRegistry:
    """按名聚合 SkillRecord，提供懒重载与工具描述生成。"""

    def __init__(self, loader: SkillLoader) -> None:
        """初始化注册表。

        Args:
            loader: SkillLoader 实例（skills_root 已注入）
        """
        self._loader = loader
        self._records: dict[str, SkillRecord] = {}
        self._last_signature: str | None = None

    def reload_if_changed(self) -> None:
        """扫描 signature 变化时重载注册表（无变化则跳过）。

        signature = 目录列表 + 各 SKILL.md (相对路径, mtime_ns, 文件大小)。
        用内容相关 signature 而非目录 mtime：编辑子目录文件不改父目录 mtime，
        只查目录 mtime 会漏掉内容修改。

        Raises:
            ValueError: 两个目录 frontmatter 声明同名（skill 名冲突，fail-fast）
        """
        signature = self._compute_signature()
        if signature == self._last_signature:
            return
        loaded = self._loader.load_all()
        records: dict[str, SkillRecord] = {}
        for rec in loaded:
            if rec.name in records:
                raise ValueError(
                    f"skill 名称冲突: {rec.name}（来自 {records[rec.name].source_path} "
                    f"与 {rec.source_path}，fail-fast 禁止静默覆盖）"
                )
            records[rec.name] = rec
        self._records = records
        self._last_signature = signature

    def get(self, name: str) -> SkillRecord | None:
        """按名取 SkillRecord；不存在返回 None（调用方降级，design D17）。

        Args:
            name: skill 名

        Returns:
            SkillRecord 或 None
        """
        return self._records.get(name)

    def names(self) -> list[str]:
        """当前注册的全部 skill 名（有序，供 description/错误提示）。"""
        return sorted(self._records)

    def to_tool_description(self, max_chars: int = 500) -> str:
        """生成 delegate_task 的可用 skill 列表文本（渐进披露，预算截断）。

        每行一个 skill："<名>: <whenToUse>"。总长度超 max_chars 时截断并追加
        "..."，保证 delegate_task.description 不撑爆工具 schema 预算。

        Args:
            max_chars: description 长度上限（字符）

        Returns:
            description 文本；无 skill 时返回"当前无可用 skill"
        """
        if not self._records:
            return "当前无可用 skill"
        lines = [f"{rec.name}: {rec.description}" for rec in self._records.values()]
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[: max_chars - 3] + "..."
        return text

    def _compute_signature(self) -> str:
        """计算当前 skills_root 的扫描 signature（目录+文件级）。

        Returns:
            sha256 hex；目录不存在时返回空字符串签名
        """
        root = self._loader.skills_root
        if not root.exists():
            return "empty"
        parts: list[str] = []
        for skill_dir in sorted(root.iterdir()):
            if not skill_dir.is_dir():
                continue
            path = skill_dir / "SKILL.md"
            if not path.exists():
                continue
            stat = path.stat()
            rel = path.relative_to(root)
            parts.append(f"{rel}:{stat.st_mtime_ns}:{stat.st_size}")
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
