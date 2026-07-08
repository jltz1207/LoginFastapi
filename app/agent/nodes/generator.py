from langchain_core.language_models import BaseChatModel

from app.agent.state import AgentState
from app.rag.prompts import QA_PROMPT


def make_generator_node(llm_with_tools: BaseChatModel):
    def generator_execution(state: AgentState) -> dict:
        context_str = "\n".join([doc.page_content for doc in state.documents])
        messages = QA_PROMPT.format_messages(
            context=context_str,
            chat_history=state.chat_messages,
            question=state.standalone_question or state.question,
        )
        response = llm_with_tools.invoke(messages)  # AIMessage; may contain tool_calls
        return {"chat_messages": [response]}
    return generator_execution
