from app.llm.factory import LLMFactory
from app.agent.state import LookupAgentState
from app.rag.chains.grade_source_chain import create_grade_source_chain


def grader_execution(state: LookupAgentState):
    llm = LLMFactory.get_model()
    chain = create_grade_source_chain(llm)
    chain_state ={
        "question": state.standalone_query or state.resolved_query or state.query,
        "context": '\n'.join([doc.content for doc in state.documents]) 
    }
    result = chain.invoke(chain_state)
    return {"grade": result.grade}