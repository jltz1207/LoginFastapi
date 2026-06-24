from langchain_core.tools import tool
from langchain_tavily import tavily_research


@tool
def tavily_web_search_tool(max_results: int, query: str)-> list:
    """Search the web for up-to-date information using Tavily.

    Use this tool when you need to find current information, research a topic,
    or look up facts that may not be in your training data.

    Args:
        max_results: Maximum number of search results to return.
        query: The search query string describing what to look up.

    Returns:
        A list of search result objects, each containing a URL, title, and content snippet.
    """
    tavily_search = tavily_research(max_results=max_results)
    results = tavily_search.invoke(query)
    return results