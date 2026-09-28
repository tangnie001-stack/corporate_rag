"""Skill 内容模型 — SkillRecord（skill 文件解析后的运行时对象）。

skill = 磁盘声明式能力文件（skills/<name>/SKILL.md）：frontmatter 声明元数据、
正文按 context 语义存为 inline_prompt（主 agent 注入执行）或 fork_body
（fork 子代理的 user message 任务内容）。skill 是内容/配置而非代码。
"""

from dataclasses import dataclass, field
from pathlib import Path


class SkillContext:
    """skill 执行上下文常量（frontmatter context 字段取值）。

    分工：inline = 指令注入主 agent 上下文、主 agent 自己执行；
    fork = 生成独立子代理隔离执行。
    """

    INLINE: str = "inline"  # inline 执行形态
    FORK: str = "fork"  # fork 执行形态


class ContextSource:
    """`context` 取值的来源（写入 SkillRecord.context_source）。

    与 SkillContext 同类且同处：两者都属于 skill 的"执行形态"语义。
    """

    DEFAULT: str = "default"  # frontmatter 未声明，按缺省取 inline
    EXPLICIT: str = "explicit"  # frontmatter 显式声明
    AUTO_OVERSIZE: str = "auto_oversize"  # 未声明但正文超预算，加载期自动改用 fork


@dataclass
class SkillRecord:
    """单个 skill 的运行时对象（由 SkillLoader 解析 SKILL.md 产出）。

    Attributes:
        name: skill 名（ASCII slug，frontmatter name 或目录名；LLM 委派匹配依据）
        description: whenToUse 一句话描述（委派匹配与前端展示依据）
        context: 执行形态（inline|fork；未声明取 inline，正文超预算时自动改 fork，见 context_source）
        inline_prompt: context=inline 时正文（短方法论，≤500 字，可含 $ARGUMENTS）
        fork_body: context=fork 时正文（子代理的 user message 任务内容）
        agent: fork 执行者预设名（空=None 时按会话选定智能体或系统默认）
        model: fork 覆盖模型（空=None 继承主 agent llm）
        allowed_tools: fork 子代理工具白名单（逗号分隔字符串解析而来）
        user_invocable: 是否允许用户 `/xxx` 调用（frontmatter 显式值或按工具只读性推导）
        disable_model_invocation: 是否禁止模型自动 delegate_task 调用（同上）
        source_path: SKILL.md 文件绝对路径（懒重载 signature 用）
        context_source: context 取值的来源（default|explicit|auto_oversize）
    """

    name: str  # skill 名（ASCII slug）
    description: str  # whenToUse 一句话描述
    context: str = SkillContext.INLINE  # inline|fork
    inline_prompt: str | None = None  # context=inline 时正文（短方法论）
    fork_body: str | None = None  # context=fork 时正文（子代理 user message）
    agent: str | None = None  # fork 执行者预设名，None=按会话选定智能体
    model: str | None = None  # fork 覆盖模型，None=继承主 agent llm
    allowed_tools: list[str] = field(
        default_factory=list
    )  # fork 子代理工具白名单（逗号分隔字符串解析）
    user_invocable: bool = True  # 允许用户 /xxx 调用
    disable_model_invocation: bool = False  # 禁止模型自动调用
    source_path: Path = field(default_factory=Path)  # SKILL.md 绝对路径
    context_source: str = (
        ContextSource.DEFAULT
    )  # context 取值来源（default|explicit|auto_oversize）
