"""agent 循环的四个 middleware（仅主角色装配）。

硬约束：实例随图构造一次、跨请求共享 ⇒ 不得在实例属性上保存 per-request
状态；读图状态一律 state.get(...)（middleware 收到的是映射）。
"""

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware, hook_config

from src.agents.graph.agent_factory import LoopState
from src.config import settings
from src.config.const import MAX_DELEGATE_BONUS
from src.core import logging as core_logging
from src.core.log_events import Event
from src.infra.llm.request_context import current_request_ctx

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


class ModelParamsMiddleware(AgentMiddleware):
    """施加温度分档与思考开关（承接原 agent_node 调用点的内联参数）。

    档位判据取**请求上下文**的绑定状态；ctx 缺失（如 CLI 评估入口）时回退
    图状态里的 kb_id（该键由节点 seed）。KB 档用「不带 temperature 键」表达
    "不传"，沿用模型构造温度。

    ⚠️ per-call extra_body 在 langchain-openai 中**整体覆盖**构造时的 extra_body
    （_get_request_payload 浅合并），故本模型不宜在 LLM_KWARGS 里配置其他
    extra_body 参数（会被本处覆盖丢弃）。
    """

    state_schema = LoopState

    def _settings(self, request: Any) -> dict[str, Any]:
        """算出本次调用的 model_settings（档位判据见类 docstring）。"""
        ctx = current_request_ctx.get()
        if ctx is not None:
            kb_bound = ctx.kb_bound
        else:
            kb_bound = bool(request.state.get("kb_id"))
        deep_thinking = bool(request.state.get("deep_thinking"))
        extra_body = {"enable_thinking": deep_thinking}
        if kb_bound:
            return {"extra_body": extra_body}
        return {
            "extra_body": extra_body,
            "temperature": settings.NON_KB_MAIN_TEMPERATURE,
        }

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        """同步钩子（脚本/测试路径）。"""
        return handler(request.override(model_settings=self._settings(request)))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        """异步钩子（生产路径）。"""
        return await handler(request.override(model_settings=self._settings(request)))


class AgentTurnBudget(AgentMiddleware):
    """回合预算（**只装配给主角色**）。

    判定放 after_model 并用 jump_to=end 结束——不注入任何消息。若放
    before_model 判下轮，会多执行一次工具、末条变 ToolMessage，答案提取就会
    把工具结果当成答案。

    委派放宽：本轮声明了 delegate_task 时**先置位**再算上限，且标志跨轮保持
    （= 今天的 `delegate_used or state._delegate_used`）。
    """

    state_schema = LoopState

    def __init__(self, limit: int, bonus: int = MAX_DELEGATE_BONUS) -> None:
        """初始化。

        Args:
            limit: 基础回合上限（模型调用次数）
            bonus: 委派放宽轮数（本轮或此前声明过 delegate_task 时叠加）
        """
        super().__init__()
        self._limit = limit  # 进程级常量
        self._bonus = bonus

    def _after_model(self, state: Any) -> dict[str, Any]:
        """自增回合计数、按有效上限判定，命中时声明 jump_to=end。

        `iteration limit` 日志与 jump 解耦：达上限即记（与该轮是否声明工具调用
        无关），jump 只在该轮仍声明工具调用时返回（正常收尾本就结束，无需 jump）。
        """
        n = state.get("_turn_count", 0) + 1
        last = state["messages"][-1]
        declared = list(getattr(last, "tool_calls", None) or [])
        delegate_now = any(c.get("name") == "delegate_task" for c in declared)
        delegate_used = delegate_now or bool(state.get("_delegate_used"))
        effective_max = self._limit + self._bonus if delegate_used else self._limit
        update: dict[str, Any] = {"_turn_count": n}
        if delegate_now:
            update["_delegate_used"] = True
        if n >= effective_max:
            core_logging.log_event(
                Event.ITERATION_LIMIT, query=state.get("query", ""), iteration=n
            )
            if declared:
                update["jump_to"] = "end"
        return update

    @hook_config(can_jump_to=["end"])
    def after_model(self, state: Any, runtime: Any) -> dict[str, Any]:
        """同步 after_model 钩子。"""
        return self._after_model(state)

    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any]:
        """异步 after_model 钩子（生产路径）。"""
        return self._after_model(state)
