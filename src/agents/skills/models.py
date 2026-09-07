"""Skill 内容模型 — SkillRecord（skill 文件解析后的运行时对象）。

skill = 磁盘声明式能力文件（skills/<name>/SKILL.md）：frontmatter 声明元数据、
正文按 context 语义存为 inline_prompt（主 agent 注入执行）或 agent_prompt
（fork 子代理 system_prompt）。skill 是内容/配置而非代码，业务解耦核心。
"""

from dataclasses import dataclass, field
from pathlib import Path


class SkillContext:
    """skill 执行上下文常量（frontmatter context 字段取值）。

    分工：inline = 指令注入主 agent 上下文、主 agent 自己执行；
    fork = 生成独立零工具子代理（create_react_agent）隔离执行。
    """

    INLINE: str = "inline"  # inline 执行形态
    FORK: str = "fork"  # fork 执行形态


@dataclass
class SkillRecord:
    """单个 skill 的运行时对象（由 SkillLoader 解析 SKILL.md 产出）。

    Attributes:
        name: skill 名（目录名；LLM 委派匹配依据）
        description: whenToUse 一句话描述（委派匹配依据）
        context: 执行形态（inline|fork，非法值回落 inline）
        inline_prompt: context=inline 时正文（短方法论，≤500 字，可含 {task} 占位）
        agent_prompt: context=fork 时正文（子代理 system_prompt）
        model: fork 覆盖模型（空=None 继承主 agent llm）
        thinking: fork 是否开思考（None=跟随主 agent 不显式设置）
        allowed_tools: fork 子代理工具白名单（**预留字段本版恒不启用**，见 design D7）
        max_iterations: fork 迭代上限（0=继承；零工具下无工具循环本版不生效，预留）
        source_path: SKILL.md 文件绝对路径（懒重载 signature 用）
    """

    name: str  # skill 名（目录名）
    description: str  # whenToUse 一句话描述
    context: str = SkillContext.INLINE  # inline|fork
    inline_prompt: str | None = None  # context=inline 时正文（短方法论，≤500 字）
    agent_prompt: str | None = None  # context=fork 时正文（子代理 system_prompt）
    model: str | None = None  # fork 覆盖模型，None=继承主 agent llm
    thinking: bool | None = None  # fork 是否开思考，None=跟随主 agent
    allowed_tools: list[str] = field(
        default_factory=list
    )  # 预留字段（本版恒不启用），见 design D7
    max_iterations: int | None = None  # 预留字段（零工具下不生效），见 design D2
    source_path: Path = field(default_factory=Path)  # SKILL.md 绝对路径
