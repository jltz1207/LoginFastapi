from langchain_core.prompts import ChatPromptTemplate


grade_source_prompt_template = """You are an expert quality assurance assistant tasked with evaluating and scoring the relevance of a retrieved context snippet to a user's question.

Your objective is to assess the context and assign a numeric score based on how useful, relevant, or necessary it is for answering the user's question.

### Grading Scale (0 to 10):
- **6 to 10 (Relevant):** The context contains information that directly addresses, partially answers, or provides necessary background to answer the question. 
  - *Use 10 for a perfect, comprehensive match.*
  - *Use 6 for borderline cases where it provides just enough of a clue or minor context to be helpful.*
- **0 to 5 (Irrelevant):** The context is unhelpful, entirely unrelated, covers a completely different topic, or cannot assist in answering the question in any way.
  - *Use 0 for completely random or unrelated noise.*

### Strict Output Format:
You must output a single integer JSON object matching the requested schema. Do not include any conversational filler, preamble, or explanations.

### Input Data:
<context>
{context}
</context>"""

GRADE_SOURCE_PROMPT= ChatPromptTemplate.from_messages(
    [
        ("system", grade_source_prompt_template),
        ("human", "User question: {question}")
    ]
)