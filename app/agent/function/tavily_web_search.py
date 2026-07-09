from langchain_community.tools.tavily_search import TavilySearchResults


def tavily_web_search(max_results: int, query: str)-> list:

    tavily_search = TavilySearchResults(max_results=max_results)
    results = tavily_search.invoke(query)
    return results