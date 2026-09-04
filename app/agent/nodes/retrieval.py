from app.agent.state import LookupAgentState

from app.rag.reranker.reranker import with_rerank
from app.rag.retriever.hybrid_retriever import HybridRetriever
from app.routing.branches.common import Chunk


# async because get_retriever() is a coroutine and with_rerank() ends in an async
# RunnableLambda — the sync .invoke() path raises on both.
async def retrieval_execution(state: LookupAgentState) -> dict:

    hybrid_retriever = await HybridRetriever((0.6, 0.4)).get_retriever(
        state.tenant_id, state.user_id, state.knowledge_base_id, top_k=4
    )
    pipeline_rerank = with_rerank(hybrid_retriever, rerank_top_k=4)
    query = state.standalone_query or state.resolved_query or state.query
    docs = await pipeline_rerank.ainvoke(query)
    chunks = [Chunk(chunk_id=doc.id, content=doc.page_content, metadata=doc.metadata) for doc in docs]
    # Entry node, so this is where the per-run budget resets. The counters live in a
    # checkpointed state keyed by thread_id, so without an explicit reset they would
    # carry over from earlier turns and exhaust the budget after a few messages.
    return {"documents": chunks, "tool_calls_count": 0, "token_count": 0}
