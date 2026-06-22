from operator import itemgetter

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnablePassthrough, RunnableSerializable
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic.v1 import BaseModel

from app.rag.chains import create_qa_prompt_chain, create_condense_question_chain
from app.rag.retriever import get_collection_retriever, format_doc_to_string

class State(BaseModel):
    chat_history: list[BaseMessage]
    question: str
    user_id: str
    class Config:
        arbitrary_types_allowed = True

def create_pipeline(user_id) -> RunnableSerializable:
    llm = ChatGoogleGenerativeAI(
        model="gemini-3.5-flash",
        temperature=0.3
    )
    condense_question_chain = create_condense_question_chain(llm)
    qa_prompt_chain = create_qa_prompt_chain(llm)
    retriever = get_collection_retriever(user_id)
    chain = RunnablePassthrough.assign(
        condensed_question = condense_question_chain # output str, condensed_question
    ).assign(
        context = itemgetter("condensed_question") | retriever | format_doc_to_string # pass string to retriever instead of a dict
    ) | qa_prompt_chain
    return chain

if __name__ == "__main__":
    load_dotenv()
    input = {
        "chat_history":  [
            AIMessage(content="Hi, How are you"),
            HumanMessage(content="Hi, How are you")
        ],
        "question": "What is FDM?"
    }
    pipelines = create_pipeline()
    pipelines.invoke(input)