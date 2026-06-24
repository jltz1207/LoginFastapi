"""
Tests for SearchingRagGraphFactory and its conditional routing.

Coverage:
  - Graph structure (nodes present, compiles successfully)
  - decider_from_grade routing logic
  - Full graph execution: retrieve → grade → generate (happy path)
  - Full graph execution: retrieve → grade → rewrite → web_search → grade → generate (search path)
"""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph.state import CompiledStateGraph

from app.agent.edges.conditional import decider_from_grade
from app.agent.graphs.searching_rag import SearchingRagGraphFactory
from app.agent.state import AgentState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(**overrides) -> AgentState:
    defaults = dict(
        user_id=uuid4(),
        knoweledge_base_id=uuid4(),
        question="What is LangGraph?",
        chat_messages=[HumanMessage(content="What is LangGraph?")],
        standalone_question="",
        documents=[],
        source="",
        loop_count=0,
        grade=None,
    )
    defaults.update(overrides)
    return AgentState(**defaults)


def _make_docs(n: int = 1) -> list[Document]:
    return [Document(page_content=f"doc content {i}", metadata={}) for i in range(n)]


# ---------------------------------------------------------------------------
# decider_from_grade
# ---------------------------------------------------------------------------

class TestDeciderFromGrade:
    def test_grade_above_5_routes_to_generate(self):
        assert decider_from_grade(_make_state(grade=6)) == "generate"

    def test_grade_10_routes_to_generate(self):
        assert decider_from_grade(_make_state(grade=10)) == "generate"

    def test_grade_5_routes_to_search(self):
        assert decider_from_grade(_make_state(grade=5)) == "search"

    def test_grade_0_routes_to_search(self):
        assert decider_from_grade(_make_state(grade=0)) == "search"

    def test_grade_none_routes_to_search(self):
        assert decider_from_grade(_make_state(grade=None)) == "search"


# ---------------------------------------------------------------------------
# Graph structure
# ---------------------------------------------------------------------------

class TestSearchingRagGraphFactoryStructure:
    def test_build_returns_compiled_state_graph(self):
        graph = SearchingRagGraphFactory.build()
        assert isinstance(graph, CompiledStateGraph)

    def test_graph_contains_all_expected_nodes(self):
        graph = SearchingRagGraphFactory.build()
        node_names = set(graph.nodes.keys())
        expected = {"retrieve_docs", "grade_docs", "web_searcher", "generate", "rewrite_question"}
        assert expected.issubset(node_names)

    def test_build_is_callable_without_instance(self):
        # @staticmethod — must be callable on the class directly
        graph = SearchingRagGraphFactory.build()
        assert graph is not None


# ---------------------------------------------------------------------------
# Full graph execution — generate path (retrieved docs are relevant)
# ---------------------------------------------------------------------------

class TestSearchingRagGraphGeneratePath:
    @patch("app.agent.nodes.retrieval.get_collection_retriever")
    @patch("app.agent.nodes.grader.create_grade_source_chain")
    @patch("app.agent.nodes.grader.LLMFactory")
    @patch("app.agent.nodes.generator.create_qa_prompt_chain")
    @patch("app.agent.nodes.generator.LLMFactory")
    def test_generate_path_returns_answer(
        self,
        mock_gen_llm,
        mock_qa_chain_factory,
        mock_grader_llm,
        mock_grade_chain_factory,
        mock_get_retriever,
    ):
        # Retriever returns one relevant doc
        mock_get_retriever.return_value.invoke.return_value = _make_docs(1)

        # Grader returns grade=8 → "generate"
        grade_result = MagicMock()
        grade_result.grade = 8
        mock_grade_chain_factory.return_value.invoke.return_value = grade_result

        # Generator returns a structured answer
        qa_result = MagicMock()
        qa_result.answer = "LangGraph is a stateful agent framework."
        qa_result.source = "retrieved_docs"
        mock_qa_chain_factory.return_value.invoke.return_value = qa_result

        graph = SearchingRagGraphFactory.build()
        result = graph.invoke(
            {
                "user_id": uuid4(),
                "knoweledge_base_id": uuid4(),
                "question": "What is LangGraph?",
                "chat_messages": [HumanMessage(content="What is LangGraph?")],
            }
        )

        assert result["source"] == "retrieved_docs"
        assert any(
            isinstance(m, AIMessage) or hasattr(m, "content")
            for m in result["chat_messages"]
        )

    @patch("app.agent.nodes.retrieval.get_collection_retriever")
    @patch("app.agent.nodes.grader.create_grade_source_chain")
    @patch("app.agent.nodes.grader.LLMFactory")
    @patch("app.agent.nodes.generator.create_qa_prompt_chain")
    @patch("app.agent.nodes.generator.LLMFactory")
    def test_generate_path_calls_retriever_with_question(
        self,
        mock_gen_llm,
        mock_qa_chain_factory,
        mock_grader_llm,
        mock_grade_chain_factory,
        mock_get_retriever,
    ):
        mock_retriever_instance = MagicMock()
        mock_retriever_instance.invoke.return_value = _make_docs(1)
        mock_get_retriever.return_value = mock_retriever_instance

        grade_result = MagicMock()
        grade_result.grade = 9
        mock_grade_chain_factory.return_value.invoke.return_value = grade_result

        qa_result = MagicMock()
        qa_result.answer = "Answer."
        qa_result.source = "docs"
        mock_qa_chain_factory.return_value.invoke.return_value = qa_result

        graph = SearchingRagGraphFactory.build()
        graph.invoke(
            {
                "user_id": uuid4(),
                "knoweledge_base_id": uuid4(),
                "question": "What is LangGraph?",
                "chat_messages": [HumanMessage(content="What is LangGraph?")],
            }
        )

        mock_retriever_instance.invoke.assert_called_once_with("What is LangGraph?")


# ---------------------------------------------------------------------------
# Full graph execution — search path (retrieved docs are irrelevant, then relevant after web search)
# ---------------------------------------------------------------------------

class TestSearchingRagGraphSearchPath:
    @patch("app.agent.nodes.retrieval.get_collection_retriever")
    @patch("app.agent.nodes.grader.create_grade_source_chain")
    @patch("app.agent.nodes.grader.LLMFactory")
    @patch("app.agent.nodes.rewriter.create_condense_question_chain")
    @patch("app.agent.nodes.rewriter.LLMFactory")
    @patch("app.agent.nodes.searcher.tavily_web_search_tool")
    @patch("app.agent.nodes.generator.create_qa_prompt_chain")
    @patch("app.agent.nodes.generator.LLMFactory")
    def test_search_path_invokes_web_searcher(
        self,
        mock_gen_llm,
        mock_qa_chain_factory,
        mock_tavily,
        mock_rewriter_llm,
        mock_condense_factory,
        mock_grader_llm,
        mock_grade_chain_factory,
        mock_get_retriever,
    ):
        mock_get_retriever.return_value.invoke.return_value = _make_docs(1)

        # First grade call: 3 (irrelevant → search); second: 7 (relevant → generate)
        grade_low = MagicMock()
        grade_low.grade = 3
        grade_high = MagicMock()
        grade_high.grade = 7
        mock_grade_chain_factory.return_value.invoke.side_effect = [grade_low, grade_high]

        # Rewriter produces a rephrased question
        mock_condense_factory.return_value.invoke.return_value = "LangGraph framework overview"

        # Web search returns one result
        mock_tavily.invoke.return_value = [
            {"title": "LangGraph Intro", "content": "LangGraph builds agents.", "url": "http://example.com"}
        ]

        qa_result = MagicMock()
        qa_result.answer = "LangGraph builds agents."
        qa_result.source = "web"
        mock_qa_chain_factory.return_value.invoke.return_value = qa_result

        graph = SearchingRagGraphFactory.build()
        result = graph.invoke(
            {
                "user_id": uuid4(),
                "knoweledge_base_id": uuid4(),
                "question": "What is LangGraph?",
                "chat_messages": [HumanMessage(content="What is LangGraph?")],
            }
        )

        mock_tavily.invoke.assert_called_once()
        assert result["source"] == "web"

    @patch("app.agent.nodes.retrieval.get_collection_retriever")
    @patch("app.agent.nodes.grader.create_grade_source_chain")
    @patch("app.agent.nodes.grader.LLMFactory")
    @patch("app.agent.nodes.rewriter.create_condense_question_chain")
    @patch("app.agent.nodes.rewriter.LLMFactory")
    @patch("app.agent.nodes.searcher.tavily_web_search_tool")
    @patch("app.agent.nodes.generator.create_qa_prompt_chain")
    @patch("app.agent.nodes.generator.LLMFactory")
    def test_search_path_uses_rewritten_question_for_web_search(
        self,
        mock_gen_llm,
        mock_qa_chain_factory,
        mock_tavily,
        mock_rewriter_llm,
        mock_condense_factory,
        mock_grader_llm,
        mock_grade_chain_factory,
        mock_get_retriever,
    ):
        mock_get_retriever.return_value.invoke.return_value = _make_docs(1)

        grade_low = MagicMock()
        grade_low.grade = 2
        grade_high = MagicMock()
        grade_high.grade = 8
        mock_grade_chain_factory.return_value.invoke.side_effect = [grade_low, grade_high]

        rewritten = "LangGraph stateful agent graph framework"
        mock_condense_factory.return_value.invoke.return_value = rewritten

        mock_tavily.invoke.return_value = [
            {"title": "LangGraph", "content": "Stateful agents.", "url": "http://docs.langgraph.com"}
        ]

        qa_result = MagicMock()
        qa_result.answer = "Stateful agents."
        qa_result.source = "web"
        mock_qa_chain_factory.return_value.invoke.return_value = qa_result

        graph = SearchingRagGraphFactory.build()
        graph.invoke(
            {
                "user_id": uuid4(),
                "knoweledge_base_id": uuid4(),
                "question": "What is LangGraph?",
                "chat_messages": [HumanMessage(content="What is LangGraph?")],
            }
        )

        # The web searcher must have been called with the rewritten question
        call_args = mock_tavily.invoke.call_args[0][0]
        assert call_args["query"] == rewritten
