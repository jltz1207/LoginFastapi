from typing import Optional

from langgraph.graph import StateGraph
from langgraph.graph.state import END, START, CompiledStateGraph
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.prebuilt import ToolNode

from app.agent.edges.conditional import decider_from_grade, decider_from_tools
from app.agent.graphs.base import BaseGraphFactory
from app.agent.llm.factory import LLMFactory
from app.agent.nodes.generator import make_generator_node
from app.agent.nodes.grader import grader_execution
from app.agent.nodes.retrieval import retrieval_execution
from app.agent.nodes.rewriter import rewriter_execution
from app.agent.nodes.searcher import web_searcher
from app.agent.state import AgentState
from app.agent.tools import SEARCHING_RAG_TOOLS


class SearchingRagGraphFactory(BaseGraphFactory):
    @staticmethod
    def build(
        checkpointer: BaseCheckpointSaver,
        tools: Optional[list] = None,
    ) -> CompiledStateGraph:
        resolved_tools = tools if tools is not None else SEARCHING_RAG_TOOLS
        llm_with_tools = LLMFactory.get_model(tools=resolved_tools)

        graph = StateGraph(AgentState)
        graph.add_node("retrieve_docs", retrieval_execution)
        graph.add_node("grade_docs", grader_execution)
        graph.add_node("web_searcher", web_searcher)
        graph.add_node("generate", make_generator_node(llm_with_tools))
        graph.add_node("rewrite_question", rewriter_execution)
        graph.add_node("tools", ToolNode(resolved_tools, messages_key="chat_messages"))

        graph.add_edge(START, "retrieve_docs")
        graph.add_edge("retrieve_docs", "grade_docs")
        graph.add_conditional_edges(
            "grade_docs",
            decider_from_grade,
            {"generate": "generate", "search": "rewrite_question"},
        )
        graph.add_edge("rewrite_question", "web_searcher")
        graph.add_edge("web_searcher", "grade_docs")
        graph.add_conditional_edges(
            "generate",
            decider_from_tools,
            {"tools": "tools", "continue": END},
        )
        graph.add_edge("tools", "generate")  # loop back after tool execution

        return graph.compile(checkpointer=checkpointer)
