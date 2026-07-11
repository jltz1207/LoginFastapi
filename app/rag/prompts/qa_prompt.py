
from langchain_core.prompts import ChatPromptTemplate


qa_system_prompt_template = '''
Your task is to answer the user's question accurately using ONLY the provided context. 
Strictly observe the following rules:
1. Ground your answer completely in the provided context. Do not use any outside or pre-trained knowledge.
2. If the answer cannot be explicitly found in the context, state exactly "I don't know." Do not attempt to extrapolate or fabricate an answer.
'''

qa_user_prompt_template = '''
<context>
{context}
</context>
User question: {question}
'''

QA_PROMPT= ChatPromptTemplate.from_messages(
    [
        ("system", qa_system_prompt_template),
        ("placeholder", "{chat_history}"),
        ("human", qa_user_prompt_template)
    ]
)