from langchain_core.language_models import BaseChatModel

from app.agent.state import AgentState
from app.rag.prompts import QA_PROMPT


def make_generator_node(llm_with_tools: BaseChatModel):
    def generator_execution(state: AgentState) -> dict:
        context_str = "\n".join([f"Document {doc_number}: " + doc.page_content for doc_number, doc in enumerate(state.documents, start=1)])
        messages = QA_PROMPT.format_messages(
            context=context_str,
            chat_history=state.chat_messages,
            question=state.standalone_question or state.question,
        )
        response = llm_with_tools.invoke(messages)  # AIMessage; may contain tool_calls
        metadata = getattr(response, "response_metadata", {})
        model_used = metadata.get("model", "unknown model")
        return {"chat_messages": [response], "model_used": model_used}
    return generator_execution
