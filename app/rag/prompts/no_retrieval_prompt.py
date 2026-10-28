from langchain_core.prompts import ChatPromptTemplate


# NO_RETRIEVAL branch (app/routing/branches/no_retrieval.py): general-knowledge questions
# answered without touching the knowledge base, so the answer must not pretend it did.
no_retrieval_system_prompt_template = '''
You are a helpful assistant answering a general-knowledge question.
Strictly observe the following rules:
1. Answer from your own general knowledge. No documents were searched for this question.
2. Do not claim that the answer comes from the user's documents or knowledge base,
   and do not add citations or source labels.
3. If you are not sure of the answer, say so instead of guessing.
4. Write in the same language as the question.
'''

no_retrieval_user_prompt_template = '''
Question: {question}
'''

NO_RETRIEVAL_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", no_retrieval_system_prompt_template),
        ("human", no_retrieval_user_prompt_template),
    ]
)
