"""agent 循环的四个 middleware（仅主角色装配）。

硬约束：实例随图构造一次、跨请求共享 ⇒ 不得在实例属性上保存 per-request
状态；读图状态一律 state.get(...)（middleware 收到的是映射）。
"""

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from src.agents.graph.agent_factory import LoopState
from src.config import settings
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
