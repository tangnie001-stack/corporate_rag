"""skill 加载器/注册表/执行器实现包（主从委派能力）。

入口：make_delegate_task（Task 6）→ SkillRegistry（懒重载）+ SkillExecutor。
"""

from src.agents.skills.delegate_task import make_delegate_task
from src.agents.skills.executor import SkillExecutor
from src.agents.skills.loader import SkillLoader
from src.agents.skills.models import SkillContext, SkillRecord
from src.agents.skills.registry import SkillRegistry

__all__ = [
    "SkillContext",
    "SkillExecutor",
    "SkillLoader",
    "SkillRecord",
    "SkillRegistry",
    "make_delegate_task",
]
