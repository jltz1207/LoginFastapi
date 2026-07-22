
from langchain_core.prompts import ChatPromptTemplate


qa_system_prompt_template = '''
Your task is to answer the user's question accurately using the provided context.
Strictly observe the following rules:
1. Ground your answer completely in the provided context. Do not use any outside or pre-trained knowledge.
2. If the provided context is empty, irrelevant, or does not contain enough information to answer the question, you MUST call the `tavily_web_search_tool` to search the web for the missing information instead of guessing or refusing to answer.
3. When calling `tavily_web_search_tool`, pass a concise search query derived from the user's question.
4. After receiving tool results, answer the question using those results, and mention that the information came from a web search.
5. Do not call the tool if the provided context already answers the question.
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