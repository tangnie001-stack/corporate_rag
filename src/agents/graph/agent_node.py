"""Agent 循环节点 — model↔tools 条件循环 + 收尾。

循环：agent_model（bind_tools 调用 LLM）→ route_agent → tools（ToolNode）→ 回 agent_model。
末轮无 tool_calls → agent_finalize（提取 answer + 读入 tool_contexts）→ format。
"""

from collections.abc import Callable

from langchain_core.messages import AIMessage, BaseMessage
from langgraph.prebuilt import ToolNode

from src.agents.graph.state import AgentState
from src.infra.llm.request_context import current_request_ctx
from src.rag.prompt import build_prompt


def make_agent_model_node(llm, tools, prompt_manager) -> Callable:
    """创建 agent 模型节点工厂：bind_tools + 初始消息注入 + 迭代计数。

    Args:
        llm: 聊天模型实例（bind_tools 后调用）
        tools: 可调用工具列表（绑定给模型的工具）
        prompt_manager: PromptManager，用于首轮 messages 为空时组装初始消息

    Returns:
        异步节点函数，接收 AgentState，返回 dict 更新 messages/_agent_iterations
    """
    model = llm.bind_tools(tools)

    def _initial_messages(state: AgentState) -> list[BaseMessage]:
        # 复用 build_prompt：system + 历史(ChatMessage→LangChain) + 当前 query
        return build_prompt(state.query, "", state._history or [], prompt_manager)

    async def agent_model(state: AgentState) -> dict:
        if state.messages:
            messages = state.messages
        else:
            messages = _initial_messages(state)
        result = await model.ainvoke(messages)
        return {"messages": [result], "_agent_iterations": state._agent_iterations + 1}

    return agent_model


def make_agent_tools_node(tools) -> Callable:
    """创建工具节点：ToolNode 包装（handle_tool_errors 错误回喂）。

    Args:
        tools: 可调用工具列表

    Returns:
        异步节点函数，接收 AgentState，返回 ToolNode 执行结果（messages 追加 ToolMessage）
    """
    node = ToolNode(tools, handle_tool_errors=True)

    async def agent_tools(state: AgentState) -> dict:
        return await node.ainvoke(state)

    return agent_tools


def make_agent_finalize_node() -> Callable:
    """创建收尾节点：提取末次 AIMessage 为 answer + 读入 tool_contexts。

    Returns:
        异步节点函数，接收 AgentState，返回 dict 更新 answer/tool_contexts
    """

    async def agent_finalize(state: AgentState) -> dict:
        if state.messages:
            last = state.messages[-1]
            answer = _extract_text(last)
        else:
            answer = ""
        ctx = current_request_ctx.get()
        if ctx is not None:
            contexts = ctx.tool_contexts
        else:
            contexts = []
        return {"answer": answer, "tool_contexts": contexts}

    return agent_finalize


def _extract_text(message: BaseMessage | None) -> str:
    """从 AIMessage 提取文本 content（str 或 content blocks）。

    Args:
        message: 消息对象，None 时返回空字符串

    Returns:
        content 的纯文本形式：str 直接返回；list 拼接 dict blocks 中 type=="text" 的 text；
        其他类型 str() 兜底
    """
    if message is None:
        return ""
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content)


def route_agent(state: AgentState) -> str:
    """agent 条件边：有 tool_calls 且未超限 → tools；否则 → agent_finalize。

    Args:
        state: 当前图状态

    Returns:
        下一节点名："tools" 或 "agent_finalize"
    """
    if state._agent_iterations >= state._max_agent_iterations:
        return "agent_finalize"
    if not state.messages:
        return "agent_finalize"
    last = state.messages[-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return "agent_finalize"
