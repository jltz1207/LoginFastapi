import asyncio

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import ToolMessage

from app.agent.state import LookupAgentState
from app.core.logging import get_logger
from app.rag.prompts import FINALIZE_PROMPT

logger = get_logger(__name__)

BUDGET_NOTICE = "Tool budget exhausted for this request; this tool call was not executed."


def make_finalize_node(plain_llm: BaseChatModel):
    """Terminal node for when `generate` asks for a tool call the budget won't pay for.

    The pending tool_calls still have to be answered even though we skip execution.
    An AIMessage carrying tool_calls with no matching ToolMessage is an invalid
    sequence and both Gemini and OpenAI reject the entire history with a 400 on the
    next turn. Because chat_history is checkpointed per thread, leaving them dangling
    would break the conversation permanently rather than just failing this request.

    `plain_llm` must be a model with no tools bound, so the answer cannot loop back
    into another tool call.
    """

    async def finalize_execution(state: LookupAgentState) -> dict:
        last_msg = state.chat_history[-1]
        pending = getattr(last_msg, "tool_calls", None) or []
        stubs = [
            ToolMessage(
                content=BUDGET_NOTICE,
                tool_call_id=tool_call["id"],
                name=tool_call.get("name", ""),
                status="error",
            )
            for tool_call in pending
        ]
        logger.warning(
            "agent.budget_exhausted",
            tool_calls_count=state.tool_calls_count,
            token_count=state.token_count,
            skipped_tool_calls=len(stubs),
        )

        context_str = "\n".join(
            f"Document {doc_number}: " + doc.content
            for doc_number, doc in enumerate(state.documents, start=1)
        )
        messages = FINALIZE_PROMPT.format_messages(
            context=context_str,
            chat_history=list(state.chat_history) + stubs,
            question=state.standalone_query or state.resolved_query or state.query,
        )

        try:
            async with asyncio.timeout(90):  # 90 second
                response = await plain_llm.ainvoke(messages)
        except TimeoutError as e:
            logger.error("Finalize invocation failed: exceeded 90 seconds.")
            raise e
        except Exception as e:
            logger.error(f"Finalize invocation failed: {e}")
            raise e

        metadata = getattr(response, "response_metadata", {})
        usage = getattr(response, "usage_metadata", None) or {}
        return {
            "chat_history": stubs + [response],
            "model_used": metadata.get("model", "unknown model"),
            "token_count": state.token_count + usage.get("total_tokens", 0),
        }

    return finalize_execution
