
from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable, RunnableSerializable

from app.rag.prompts import condense_question_prompt

'''
input: {chat_history} + {question}
output: str
'''
def create_condense_question_chain(llm: BaseChatModel) -> RunnableSerializable:
    chain =  condense_question_prompt | llm | StrOutputParser()
    return chain
