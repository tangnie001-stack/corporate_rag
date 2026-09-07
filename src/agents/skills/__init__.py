"""skill 加载器/注册表/执行器实现包（主从委派能力）。

入口：make_delegate_task（Task 6）→ SkillRegistry（懒重载）+ SkillExecutor。
"""

from src.agents.skills.models import SkillContext, SkillRecord

__all__ = ["SkillContext", "SkillRecord"]
