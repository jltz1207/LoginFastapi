from app.agent.edges.conditional import decider_from_grade
from app.agent.graphs.base import BaseGraphFactory
from langgraph.graph import StateGraph
from langgraph.graph.state import END, START, CompiledStateGraph

from app.agent.nodes.generator import generator_execution
from app.agent.nodes.grader import grader_execution
from app.agent.nodes.retrieval import retrieval_execution
from app.agent.nodes.rewriter import rewriter_execution
from app.agent.nodes.searcher import web_searcher
from app.agent.state import AgentState

class SearchingRagGraphFactory(BaseGraphFactory):
    @staticmethod
    def build() -> CompiledStateGraph:
        graph = StateGraph(AgentState)
        graph.add_node("retrieve_docs", retrieval_execution)
        graph.add_node("grade_docs", grader_execution)
        graph.add_node("web_searcher", web_searcher)
        graph.add_node("generate", generator_execution)
        graph.add_node("rewrite_question", rewriter_execution)

        graph.add_edge(START, "retrieve_docs")
        graph.add_edge("retrieve_docs", "grade_docs")
        graph.add_conditional_edges(
            "grade_docs",
            decider_from_grade,
            {
                "generate": "generate",
                "search": "rewrite_question" # if irrelevant source are provided
            }
        )
        graph.add_edge("rewrite_question", "web_searcher")
        graph.add_edge("web_searcher", "grade_docs") # replace the orginal source, because they are irrelevant
        graph.add_edge("generate", END)
        return graph.compile()
