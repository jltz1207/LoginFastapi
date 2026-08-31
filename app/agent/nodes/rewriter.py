from app.llm.factory import LLMFactory
from app.agent.state import LookupAgentState
from app.rag.chains.condense_question_chain import create_condense_question_chain


def rewriter_execution(state: LookupAgentState):
    llm = LLMFactory.get_model()
    chain = create_condense_question_chain(llm)
    chain_state = {
        "chat_history": state.chat_history,
        "question": state.standalone_query or state.resolved_query or state.query
    }
    standalone_query = chain.invoke(chain_state)
    return {
        "loop_count": state.loop_count + 1,
        "standalone_query": standalone_query
    }
