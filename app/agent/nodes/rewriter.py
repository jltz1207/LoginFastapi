from app.agent.llm.factory import LLMFactory
from app.agent.state import AgentState
from app.rag.chains.condense_question_chain import create_condense_question_chain


def rewriter_execution(state: AgentState):
    llm = LLMFactory.get_model()
    chain = create_condense_question_chain(llm)
    chain_state = {
        "chat_history": state.chat_messages,
        "question": state.question
    }
    standalone_question = chain.invoke(chain_state)
    return {
        "loop_count": state.loop_count + 1,
        "standalone_question": standalone_question 
    }
