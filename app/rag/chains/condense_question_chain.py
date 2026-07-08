
from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable, RunnableSerializable

from app.rag.prompts import CONDENSE_PROMPT

'''
input: {chat_history} + {question}
output: str
'''
def create_condense_question_chain(llm: BaseChatModel) -> RunnableSerializable:
    chain =  CONDENSE_PROMPT | llm | StrOutputParser()
    return chain
