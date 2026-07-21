import asyncio

from langchain_core.language_models import BaseChatModel

from app.agent.state import AgentState
from app.core.logging import get_logger
from app.rag.prompts import QA_PROMPT

logger = get_logger(__name__)

def make_generator_node(llm_with_tools: BaseChatModel):
    async def generator_execution(state: AgentState) -> dict:
        try:
            context_str = "\n".join([f"Document {doc_number}: " + doc.page_content for doc_number, doc in enumerate(state.documents, start=1)])
            messages = QA_PROMPT.format_messages(
                context=context_str,
                chat_history=state.chat_messages,
                question=state.standalone_question or state.question,
            )
            response = ""
            async with asyncio.timeout(90): # 90 second
                response = await llm_with_tools.ainvoke(messages)  # AIMessage; may contain tool_calls
            metadata = getattr(response, "response_metadata", {})
            model_used = metadata.get("model", "unknown model")
            return {"chat_messages": [response], "model_used": model_used}
        except TimeoutError as e:
            logger.error(f"LLM Invocation failed: The entire graph execution exceeds 90 second.")
            print("The entire graph execution exceeds 90 second.")
            raise e
        except Exception as e:
            logger.error(f"LLM Invocation failed: {e}")
    return generator_execution

'''
To be clear about what does and doesn't need to change:
- LangGraph itself is fine with a mix of sync and async nodes — it already runs sync nodes (retrieve_docs, grade_docs, rewrite_question, web_searcher) in a background thread when the graph is driven via .ainvoke()/.ast
ream_events(), so they don't need to change.
- Only generate needs to switch to ainvoke, because that's the one node whose output we actually want to stream to the user token-by-token. The other nodes produce internal/structured output (grades, rewritten question
s, search results) that isn't shown to the user as it streams, so there's no benefit to touching them.

If generator_execution stayed sync and calling .invoke(), the SSE endpoint would technically still "work" but you'd get the entire answer delivered in one chunk right at the end — functionally identical to today's bloc
king /chat, just wrapped in SSE framing for no benefit. The async change is what actually buys you the token-by-token streaming.
'''