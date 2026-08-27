from app.agent.state import AgentState

from app.rag.reranker.reranker import with_rerank
from app.rag.retriever.basic_retriever import get_collection_retriever
from app.rag.retriever.hybrid_retriever import HybridRetriever


def retrieval_execution(state: AgentState) -> dict:
    
    hybrid_retriever = HybridRetriever((0.6, 0.4)).get_retriever(str(state.user_id), str(state.knowledge_base_id), top_k=4)
    pipeline_rerank = with_rerank(hybrid_retriever, rerank_top_k=4)
    query = state.standalone_question or state.question
    docs = pipeline_rerank.invoke(query)
    return {"documents": docs}
