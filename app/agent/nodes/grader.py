from app.agent.llm.factory import LLMFactory
from app.agent.state import AgentState
from app.rag.chains.grade_source_chain import create_grade_source_chain


def grader_execution(state: AgentState):
    llm = LLMFactory.get_model()
    chain = create_grade_source_chain(llm)
    chain_state ={
        "question": state.standalone_question or state.question,
        "context": '\n'.join([doc.page_content for doc in state.documents]) 
    }
    result = chain.invoke(chain_state)
    return {"grade": result.grade}