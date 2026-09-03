
from langchain_core.prompts import ChatPromptTemplate


finalize_system_prompt_template = '''
Your task is to answer the user's question using only the material already gathered.

The tool budget for this request has been exhausted, so no further tool calls are
possible. Strictly observe the following rules:
1. Answer using the provided context together with any tool results already present
   in the conversation above.
2. Do NOT request another search and do not claim you will look anything up.
3. If the gathered material genuinely does not answer the question, say so plainly
   and state what information is missing.
'''

finalize_user_prompt_template = '''
<context>
{context}
</context>
User question: {question}
'''

FINALIZE_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", finalize_system_prompt_template),
        ("placeholder", "{chat_history}"),
        ("human", finalize_user_prompt_template),
    ]
)
