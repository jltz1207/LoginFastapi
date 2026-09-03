from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import ToolNode

from app.agent.budget import _limits
from app.core.logging import get_logger

logger = get_logger(__name__)

TRUNCATION_MARKER = "\n…[truncated: tool result exceeded the per-call budget]"


def make_bounded_tool_node(tools: list, messages_key: str = "chat_history"):
    """ToolNode wrapper capping how much of each tool result flows back into context.

    Every loop back into `generate` re-sends the whole history, so an oversized tool
    result is paid for on each subsequent turn, not just the one that fetched it.
    Truncation is by characters rather than via `app.utils.token_counter`: that helper
    is tiktoken-based, which is only an approximation for the Gemini/DeepSeek models
    configured here, and this runs on every single tool call.
    """
    inner = ToolNode(tools, messages_key=messages_key)

    async def bounded_tool_execution(state, config: RunnableConfig) -> dict:
        limit = _limits(config).max_tool_result_chars
        result = await inner.ainvoke(state, config)

        capped = []
        for message in result.get(messages_key, []):
            content = message.content
            if isinstance(content, str) and len(content) > limit:
                logger.warning(
                    "agent.tool_result_truncated",
                    tool=getattr(message, "name", ""),
                    original_chars=len(content),
                    limit=limit,
                )
                message = message.model_copy(
                    update={"content": content[:limit] + TRUNCATION_MARKER}
                )
            capped.append(message)

        return {**result, messages_key: capped}

    return bounded_tool_execution
