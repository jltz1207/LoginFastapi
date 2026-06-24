from app.agent.llm.factory import LLMFactory
from app.agent.state import AgentState
from app.rag.chains.qa_prompt_chain import create_qa_prompt_chain


def generator_execution(state: AgentState):
    llm = LLMFactory.get_model()
    qa_prompt_chain = create_qa_prompt_chain(llm)
    context_str= "\n".join([doc.page_content for doc in state.documents])
    qa_state = {
        "context": context_str,
        "chat_messages": state.chat_messages,
        "question": state.standalone_question or state.question
    }
    result = qa_prompt_chain.invoke(qa_state)
    return {
        "chat_messages": [result.answer],
        "source": result.source
    } 