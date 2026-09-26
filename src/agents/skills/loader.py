"""SkillLoader — 扫描 skills/<name>/SKILL.md 并解析 frontmatter + 正文。

SKILL.md 结构：YAML frontmatter（--- 包裹）+ 正文。frontmatter 字段：
name/description/context/model/allowed-tools/agent/user-invocable/disable-model-invocation。
解析规则：
- name 缺省用目录名；description 缺省用正文首段
- name 必须是 ASCII slug（CAPABILITY_NAME_PATTERN），否则记 warning 并跳过该 skill
- context 未声明取 fork；非法值抛错 → 该 skill 跳过加载（同非法 name）
- context=fork 但未声明 allowed-tools 记 warning（子代理零工具，分析型 skill 属有意设计）
- allowed-tools 用逗号分隔字符串书写，内部转 list
- 正文按 context 存 inline_prompt（inline）或 fork_body（fork）
- thinking / max-iterations 已废弃：忽略并记 warning
- 只扫一层 skills/<name>/SKILL.md，不递归（目录即 skill 边界）
"""

import warnings
from pathlib import Path

import yaml

from src.agents.skills.invocation import derive_invocation_flags
from src.agents.skills.models import SkillContext, SkillRecord
from src.agents.tools.readonly import readonly_map
from src.config.const import CAPABILITY_NAME_PATTERN, DEPRECATED_SKILL_FIELDS


class SkillLoader:
    """从 skills_root 扫描并解析全部 SKILL.md 为 SkillRecord。"""

    def __init__(
        self, skills_root: Path, tool_readonly: dict[str, bool] | None = None
    ) -> None:
        """初始化加载器。

        Args:
            skills_root: skills 内容库根目录（含 <name>/SKILL.md 子目录）
            tool_readonly: 工具名 -> 是否只读 映射（供双轴默认推导）；None 表示
                在每次解析时读进程级声明表 readonly_map()（延迟解析：工具注册
                晚于 loader 构造，构造期快照会漏掉后注册的工具）
        """
        self.skills_root = skills_root
        # 存原值（含 None），不在此快照 readonly_map()：生产在工具注册前构造 loader，
        # 快照会让双轴推导读到空表；延迟到解析时读取才能反映当前注册结果
        self._tool_readonly = tool_readonly

    def load_all(self) -> list[SkillRecord]:
        """扫描 skills_root 下全部 skill，解析为 SkillRecord 列表。

        Returns:
            解析成功的 SkillRecord 列表；目录不存在或为空时返回空列表。
            单文件解析失败（YAML 非法 / 字段类型不符 / 名称非法 / 读取 IO 错误）
            记 warning 并跳过（fail-open，坏 skill 不拖垮整体）。
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
            except (yaml.YAMLError, ValueError, OSError) as exc:
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
            ValueError: frontmatter 非 dict / 字段类型不符 / 名称为空或非 ASCII slug
            OSError: 读取 SKILL.md 文件失败（读错误由 load_all 捕获并跳过）
        """
        raw = path.read_text(encoding="utf-8")
        meta, body = self._split_frontmatter(raw)
        self._warn_deprecated_fields(meta)
        name = self._resolve_name(meta, fallback_name)
        description = self._resolve_description(meta, body)
        context = self._resolve_context(meta)
        inline_prompt, fork_body = self._resolve_body(context, body)
        allowed_tools = self._resolve_allowed_tools(meta)
        self._warn_fork_without_tools(name, context, allowed_tools)
        user_invocable, disable_model_invocation = self._resolve_invocation_flags(
            meta, allowed_tools, name
        )
        return SkillRecord(
            name=name,
            description=description,
            context=context,
            inline_prompt=inline_prompt,
            fork_body=fork_body,
            agent=self._resolve_optional_str(meta, "agent"),
            model=self._resolve_optional_str(meta, "model"),
            allowed_tools=allowed_tools,
            user_invocable=user_invocable,
            disable_model_invocation=disable_model_invocation,
            source_path=path.resolve(),
        )

    def _resolve_name(self, meta: dict, fallback_name: str) -> str:
        """解析并校验 skill 名（ASCII slug 约束）。"""
        name = meta.get("name")
        if not isinstance(name, str) or not name:
            name = fallback_name
        if not CAPABILITY_NAME_PATTERN.match(name):
            raise ValueError(
                f"名称非法（仅允许 ASCII slug：字母数字/下划线/连字符，且不以连字符开头）: {name!r}"
            )
        return name

    def _warn_deprecated_fields(self, meta: dict) -> None:
        """读到时忽略已废弃字段并记 warning（不写入记录）。"""
        for key in DEPRECATED_SKILL_FIELDS:
            if key in meta:
                warnings.warn(
                    f"skill frontmatter 的 {key} 已废弃，已忽略（迭代上限改由执行者 maxTurns 控制）"
                )

    def _resolve_description(self, meta: dict, body: str) -> str:
        """description 缺省时用正文首段兜底。"""
        description = meta.get("description")
        if not isinstance(description, str) or not description:
            return self._first_paragraph(body)
        return description

    def _warn_fork_without_tools(
        self, name: str, context: str, allowed_tools: list[str]
    ) -> None:
        """fork 未声明 allowed-tools 时记 warning：子代理会拿不到任何工具。

        fork 的子代理工具 = 执行者 tools ∩ allowed-tools，白名单为空即零工具
        （见 fork_tools.select_fork_tools）。对「分析型 skill」这是有意设计——材料由
        主 agent 预检索后经 task 传入；但对遗忘了白名单的新 skill 是踩坑点，故提示。
        inline 不适用（白名单只约束子代理工具，主 agent 工具不受其影响）。
        """
        if context == SkillContext.FORK and not allowed_tools:
            warnings.warn(
                f"skill {name} 声明 context: fork 但未声明 allowed-tools —— "
                "子代理将拿不到任何工具；分析型 skill 需由主 agent 预检索材料并经 task 传入"
            )

    def _resolve_context(self, meta: dict) -> str:
        """解析 context；未声明取 fork，非法值抛 ValueError 由 load_all 跳过该 skill。

        未声明取 fork：inline 会把 skill 正文写进主 agent 的会话历史并长期占用其历史
        预算（超限后静默被裁，见 requirements_pool F-13），而 fork 的失效可见（执行
        路径不对）。

        非法值不回落任一模式，而是抛错让 load_all 跳过该 skill（与非法 name 同款）：
        降级到 inline 是静默失效，降级到 fork 会得到零工具子代理，两者都在猜作者本意；
        拼错应立即暴露给作者修正。
        """
        context = meta.get("context", SkillContext.FORK)
        if context not in (SkillContext.INLINE, SkillContext.FORK):
            raise ValueError(f"context 非法值 {context!r}（仅允许 inline / fork）")
        return context

    def _resolve_body(self, context: str, body: str) -> tuple[str | None, str | None]:
        """按 context 把正文落到 inline_prompt 或 fork_body。"""
        if context == SkillContext.INLINE:
            return body, None
        return None, body

    def _resolve_optional_str(self, meta: dict, key: str) -> str | None:
        """解析可选的字符串字段（非字符串或空串视为未声明）。"""
        value = meta.get(key)
        if not isinstance(value, str) or not value:
            return None
        return value

    def _resolve_invocation_flags(
        self, meta: dict, allowed_tools: list[str], name: str
    ) -> tuple[bool, bool]:
        """解析双轴：显式声明优先，未声明则按工具只读性推导（fail-safe）。

        未显式注入只读表时，在解析时读取当前进程级声明表 readonly_map()，
        以反映构造之后才完成的工具注册。
        """
        if self._tool_readonly is None:
            table = readonly_map()
        else:
            table = self._tool_readonly
        declared_user = meta.get("user-invocable")
        declared_model = meta.get("disable-model-invocation")
        derived_user, derived_model = derive_invocation_flags(allowed_tools, table)
        if isinstance(declared_user, bool):
            user_invocable = declared_user
        else:
            user_invocable = derived_user
        if isinstance(declared_model, bool):
            disable_model_invocation = declared_model
        else:
            disable_model_invocation = derived_model
            if derived_model:
                warnings.warn(
                    f"skill {name} 含非只读工具但未显式声明 disable-model-invocation，已默认关闭模型自动调用"
                    "（如需开放请显式写 disable-model-invocation: false）"
                )
        if not user_invocable and disable_model_invocation:
            warnings.warn(
                f"skill {name} 既不可用户调用也不可模型调用（死 skill），请检查双轴声明"
            )
        return user_invocable, disable_model_invocation

    def _resolve_allowed_tools(self, meta: dict) -> list[str]:
        """解析 allowed-tools：支持逗号分隔字符串（主流写法）与 YAML 列表（兼容）。"""
        value = meta.get("allowed-tools")
        if isinstance(value, str):
            items = [part.strip() for part in value.split(",")]
        elif isinstance(value, list):
            items = [str(part).strip() for part in value]
        else:
            items = []
        return [item for item in items if item]

    def _split_frontmatter(self, raw: str) -> tuple[dict, str]:
        """把 SKILL.md 拆成 (frontmatter dict, 正文)。

        Args:
            raw: 文件全文

        Returns:
            (frontmatter dict, 正文 str)；无 frontmatter 时返回 (空 dict, 全文)

        Raises:
            yaml.YAMLError: frontmatter 不是合法 YAML
            ValueError: frontmatter 不是 mapping
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
