from langchain_core.documents import Document

from app.agent.state import AgentState
from app.agent.tools.web_search import tavily_web_search


async def web_searcher(state: AgentState) -> dict:
    query = state.standalone_question or state.question
    results = await tavily_web_search(max_results=5, query=query)
    docs = [
        Document(
            page_content=f"{r['title']}\n{r['content']}",
            metadata={"source": r.get("url", "")}
        )
        for r in results
    ]
    return {"documents": docs}