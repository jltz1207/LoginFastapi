from langchain_core.prompts import ChatPromptTemplate


# Map step: one section of a long document → a partial summary. Also used to collapse a
# batch of partial summaries when they are still too long for the reduce step.
document_summary_map_system_prompt_template = '''
You are summarizing one part of a longer document so the parts can be combined later.
Strictly observe the following rules:
1. Use only the text provided. Do not add outside knowledge.
2. Keep every distinct topic the text covers, including minor ones, plus concrete facts
   such as names, dates and figures.
3. Do not assume what questions will be asked later; stay neutral.
4. Write in the same language as the text.
'''

document_summary_map_user_prompt_template = '''
<text>
{text}
</text>
Summarize this part in at most {target_chars} characters.
'''

DOCUMENT_SUMMARY_MAP_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", document_summary_map_system_prompt_template),
        ("human", document_summary_map_user_prompt_template),
    ]
)


# Reduce step: the whole document (short documents) or the partial summaries (long
# documents) → the one document summary stored in documents.summary and read by the
# GLOBAL branch (app/routing/branches/global_summary.py).
document_summary_reduce_system_prompt_template = '''
You are writing the single summary that represents a whole document in a document index.
Strictly observe the following rules:
1. Use only the text provided. Do not add outside knowledge.
2. Start with a "Topics:" line listing every topic the document covers, including minor
   ones, separated by semicolons.
3. Then write one neutral paragraph describing what the document is about. Do not assume
   what questions will be asked later.
4. Write in the same language as the text.
'''

document_summary_reduce_user_prompt_template = '''
<text>
{text}
</text>
Write the document summary in at most {target_chars} characters.
'''

DOCUMENT_SUMMARY_REDUCE_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", document_summary_reduce_system_prompt_template),
        ("human", document_summary_reduce_user_prompt_template),
    ]
)
