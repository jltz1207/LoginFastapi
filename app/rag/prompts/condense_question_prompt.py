from langchain_core.prompts import ChatPromptTemplate


condense_prompt_template = '''Given a chat history and the latest user question which might reference context in the chat history, formulate a standalone question which can be understood without the chat history. 

Do NOT answer the question. Just reformulate it if needed, or return it exactly as it is if it is already a standalone question. Keep it concise and search-optimized.'''

CONDENSE_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", condense_prompt_template),
        ("placeholder", "{chat_history}"),
        ("human", "{question}")
    ]
)