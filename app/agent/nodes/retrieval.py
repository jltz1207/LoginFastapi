from app.agent.state import LookupAgentState

from app.rag.reranker.reranker import with_rerank
from app.rag.retriever.basic_retriever import get_collection_retriever
from app.rag.retriever.hybrid_retriever import HybridRetriever


def retrieval_execution(state: LookupAgentState) -> dict:

    hybrid_retriever = HybridRetriever((0.6, 0.4)).get_retriever(state.user_id, state.knowledge_base_id, top_k=4)
    pipeline_rerank = with_rerank(hybrid_retriever, rerank_top_k=4)
    query = state.standalone_query or state.resolved_query or state.query
    docs = pipeline_rerank.invoke(query)
    # Entry node, so this is where the per-run budget resets. The counters live in a
    # checkpointed state keyed by thread_id, so without an explicit reset they would
    # carry over from earlier turns and exhaust the budget after a few messages.
    return {"documents": docs, "tool_calls_count": 0, "token_count": 0}
