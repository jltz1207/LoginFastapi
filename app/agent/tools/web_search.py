from langchain_core.tools import tool
from langchain_community.tools.tavily_search import TavilySearchResults

MAX_SEARCH_RESULTS = 5


async def tavily_web_search(max_results: int, query: str) -> list:
    tavily_search = TavilySearchResults(max_results=max_results)
    results = await tavily_search.ainvoke(query)
    return results


@tool
async def tavily_web_search_tool(max_results: int, query: str)-> list:
    """Search the web for up-to-date information using Tavily.

    Use this tool when you need to find current information, research a topic,
    or look up facts that may not be in your training data.

    Args:
        max_results: Maximum number of search results to return.
        query: The search query string describing what to look up.

    Returns:
        A list of search result objects, each containing a URL, title, and content snippet.
    """
    # max_results is model-controlled, so clamp it: every result is re-sent on each
    # subsequent loop through `generate`, and an unbounded value blows the budget.
    results = await tavily_web_search(min(max(max_results, 1), MAX_SEARCH_RESULTS), query)
    return results
