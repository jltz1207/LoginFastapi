
from enum import Enum

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableSerializable
from pydantic.v1 import BaseModel, Field

from app.rag.prompts.grade_source_prompt import GRADE_SOURCE_PROMPT



class GradeSource(BaseModel):
    grade: int = Field(
        description=(
            "A relevance score from 0 to 10. "
            "Scores 0-5 mean the source is irrelevant or unhelpful to the question. "
            "Scores 6-10 mean the source is relevant, accurate, and helps answer the question. "
            "Use 0 for completely unrelated content and 10 for a perfect, comprehensive match."
        ),
        ge=0,
        le=10
    )


def create_grade_source_chain(llm: BaseChatModel) -> RunnableSerializable:
    structured_llm = llm.with_structured_output(GradeSource)
    chain = GRADE_SOURCE_PROMPT | structured_llm
    return chain
