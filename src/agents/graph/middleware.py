"""agent 循环的四个 middleware（仅主角色装配）。

硬约束：实例随图构造一次、跨请求共享 ⇒ 不得在实例属性上保存 per-request
状态；读图状态一律 state.get(...)（middleware 收到的是映射）。
"""

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from src.agents.graph.agent_factory import LoopState

logger = logging.getLogger(__name__)


class SystemMessagesMiddleware(AgentMiddleware):
    """把节点 seed 的 system 段施加到每次模型调用。

    未绑定 KB 时有两段（主 system + 未绑定提示），故不能用只支持单条的
    @dynamic_prompt；改用 override(system_message=第一条, messages=[第二条, ...])。
    """

    state_schema = LoopState

    def _apply(self, request: Any) -> Any:
        """把 state 里的 system 段施加到 request（首条进 system_message，其余前插）。"""
        system_messages = request.state.get("_system_messages", []) or []
        if not system_messages:
            return request
        first = system_messages[0]
        rest = list(system_messages[1:])
        return request.override(
            system_message=first, messages=[*rest, *request.messages]
        )

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        """同步钩子（脚本/测试路径）。"""
        return handler(self._apply(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        """异步钩子（生产路径）。"""
        return await handler(self._apply(request))
