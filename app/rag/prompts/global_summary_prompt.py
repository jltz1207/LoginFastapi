from langchain_core.prompts import ChatPromptTemplate


# Map step of the GLOBAL branch (app/routing/branches/global_summary.py): a batch of
# document summaries → one intermediate summary, used only while there are still too many
# summaries for a single answer call. It must not answer the question yet.
global_summary_map_system_prompt_template = '''
You are condensing document summaries so they can be combined later to answer a question.
Strictly observe the following rules:
1. Use only the summaries provided. Do not add outside knowledge.
2. Keep only the points relevant to the question, with concrete facts such as names,
   dates and figures.
3. Keep any document name or source label attached to the points it belongs to.
4. Do not answer the question; write a neutral summary of the relevant points.
5. Write in the same language as the question.
'''

global_summary_map_user_prompt_template = '''
<summaries>
{summaries}
</summaries>
Question: {query}
Summarize the points in these summaries that are relevant to the question.
'''

GLOBAL_SUMMARY_MAP_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", global_summary_map_system_prompt_template),
        ("human", global_summary_map_user_prompt_template),
    ]
)


# Final step of the GLOBAL branch: the remaining summaries → the answer the user sees.
global_summary_answer_system_prompt_template = '''
You are answering a question about a whole knowledge base, using summaries of its documents.
Strictly observe the following rules:
1. Use only the summaries provided. Do not add outside knowledge.
2. Answer the question directly.
3. If the summaries do not contain enough information to answer, say so clearly.
4. Write in the same language as the question.
'''

global_summary_answer_user_prompt_template = '''
<summaries>
{summaries}
</summaries>
Question: {query}
'''

GLOBAL_SUMMARY_ANSWER_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", global_summary_answer_system_prompt_template),
        ("human", global_summary_answer_user_prompt_template),
    ]
)
