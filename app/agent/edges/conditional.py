from typing import Literal

from app.agent.state import AgentState


def decider_from_grade(state: AgentState) -> Literal["generate", "search"]:
    if (state.grade is not None and state.grade > 5) or state.loop_count >= 2:
        return "generate"
    else:
        return "search"

def decider_from_tools(state: AgentState) -> Literal["tools", "continue"]:
    last_msg = state.chat_messages[-1]
    if last_msg.tool_calls:
        return "tools"
    return "continue"