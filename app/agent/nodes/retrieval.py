from app.agent.state import AgentState
from app.rag.retriever import get_collection_retriever


def retrieval_execution(state: AgentState) -> dict:
    search_kwag = {
        "k": 3,
        "filter": {
            "knowledge_base_id": state.knoweledge_base_id
        }
    }
    retriever = get_collection_retriever(state.user_id, search_kwag=search_kwag)
    query = state.standalone_question or state.question
    docs = retriever.invoke(query)
    return {"documents": docs}
