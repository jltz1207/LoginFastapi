from typing import Literal
from langchain_core.runnables import RunnableConfig

from app.agent.budget import _limits
from app.agent.state import LookupAgentState


def decider_from_grade(state: LookupAgentState) -> Literal["generate", "search"]:
    if (state.grade is not None and state.grade > 5) or state.loop_count >= 2:
        return "generate"
    else:
        return "search"

def decider_from_tools(state: LookupAgentState, config: RunnableConfig) -> Literal["tools", "finalize", "continue"]:
    if not state.chat_history:
        return "continue"

    last_msg = state.chat_history[-1]
    if last_msg.type != "ai" or not getattr(last_msg, "tool_calls", None):
        return "continue"

    # There are pending tool_calls. Over budget we must NOT fall through to END:
    # that would checkpoint an AIMessage whose tool_calls have no ToolMessage reply,
    # which the provider rejects on the next turn. `finalize` answers them instead.
    budget_limits_config = _limits(config)
    over_budget = (
        state.tool_calls_count > budget_limits_config.max_tool_calls
        or state.token_count > budget_limits_config.max_tokens
    )
    # `generate` already folded this round's requested calls into the counter, so
    # `>` admits the Nth tool call and stops the (N+1)th.
    return "finalize" if over_budget else "tools"