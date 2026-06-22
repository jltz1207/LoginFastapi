from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableSerializable
from pydantic import BaseModel, Field
from app.rag.prompts.qa_prompt import QA_PROMPT

'''
input: {context} + {chat_history} + {question}
Output: str
'''

class OutputState(BaseModel):
    answer: str = Field(description="The direct answer to the user's question based strictly on the context.")
    source: str = Field(description="A comma-separated list or description of the sources used from the context, or 'None'.")

def create_qa_prompt_chain(llm:BaseChatModel) -> RunnableSerializable:
    structured_llm = llm.with_structured_output(OutputState)
    chain = QA_PROMPT | structured_llm 
    return chain