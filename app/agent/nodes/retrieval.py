from app.agent.state import AgentState
from app.rag.retriever import get_collection_retriever


def retrieval_execution(state: AgentState) -> dict:
    retriever = get_collection_retriever(state.user_id, search_kwag={"k": 5})
    query = state.standalone_question or state.question
    docs = retriever.invoke(query)
    return {"documents": docs}
