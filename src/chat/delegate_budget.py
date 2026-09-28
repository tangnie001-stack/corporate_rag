"""会话级委派预算 —— 进程内计数、TTL 惰性清理（design D10）。

用途：给"模型裁量的委派"（`delegate_task` 的定点与通用分支）加一道会话级闸门，
防失控刷子代理。用户显式 `/xxx` 直出走 `skill_direct`，**不经** `delegate_task`，
故天然不消耗预算。

约束与语义：
- **进程内**状态（与 `task_registry` / `streaming_manager` 同假设：生产单 worker，
  流式状态在进程内；见 docs/agents/defensive-patterns.md）。进程重启即清。
- **取消/异常不回滚**：已发起即计数（计数发生在子代理启动前），失败不退还额度——
  否则模型可以靠"发起即失败"绕过闸门。
- **等效 30 分钟滚动窗口**：TTL 惰性清理（`BUDGET_TTL_SECONDS = 1800`）会把超过 TTL
  未计数的会话计数归零，故"会话级"实为"30 分钟窗口内的委派次数上限"——TTL 过期即
  窗口滚动；会话删除仍会显式 `reset`。
- **只记不判**：本模块不读 settings，`limit` 由调用方传入（保持可测、无配置耦合）。
- **不设嵌套深度上限**（design D10）：子代理不持有 `delegate_task`（禁用集硬保证），
  委派深度恒为 1，故深度上限是没有对象的配置。**勿照抄外部实现的"5 层封顶"。**
"""

import time

BUDGET_TTL_SECONDS = 1800  # 条目 TTL（默认 30min），惰性清理（与 task_registry 同款）


class SessionDelegateBudget:
    """会话级委派计数（进程内，键为 session_id）。

    Attributes:
        ttl_seconds: 条目 TTL（秒）；`check_and_incr` 每次调用会顺带惰性清理
    """

    def __init__(self, ttl_seconds: float = BUDGET_TTL_SECONDS) -> None:
        self._counts: dict[str, int] = {}  # session_id -> 已发起委派次数
        self._touched: dict[str, float] = {}  # session_id -> 最近计数时间（供 TTL）
        self.ttl_seconds = ttl_seconds  # 条目 TTL（秒）

    def used(self, session_id: str) -> int:
        """返回该会话已发起的委派次数（未计数过为 0）。"""
        return self._counts.get(session_id, 0)

    def check_and_incr(self, session_id: str, limit: int) -> bool:
        """检查额度并占用一次。

        Args:
            session_id: 会话 id
            limit: 本次会话允许的委派次数上限

        Returns:
            True = 额度可用且已计数；False = 已触顶（**不**计数）
        """
        self.sweep_expired()
        current = self._counts.get(session_id, 0)
        if current >= limit:
            self._touched[session_id] = time.time()
            return False
        self._counts[session_id] = current + 1
        self._touched[session_id] = time.time()
        return True

    def reset(self, session_id: str) -> None:
        """清除该会话计数（会话删除/历史清空时调用）。"""
        self._counts.pop(session_id, None)
        self._touched.pop(session_id, None)

    def sweep_expired(self) -> None:
        """惰性清理：超过 TTL 未计数的会话整条删除（防进程内 dict 只增不减）。"""
        now = time.time()
        stale = [
            sid
            for sid, touched in self._touched.items()
            if now - touched > self.ttl_seconds
        ]
        for sid in stale:
            self.reset(sid)


# 模块级共享实例（与 task_registry 同生命周期假设）
delegate_budget = SessionDelegateBudget()
