from langchain_core.prompts import ChatPromptTemplate


# Decompose step of the MULTI_HOP branch (app/routing/branches/multi_hop.py): given the
# question and what earlier hops retrieved, list the sub-queries for the next round.
# The excerpts are what lets it decide "enough information" and build B's query from A's result.
multi_hop_decompose_system_prompt_template = '''
你在幫一個需要多次檢索才能回答的問題做分解。
Strictly observe the following rules:
1. 根據原始問題，以及已經查過的 sub-query 和它們檢索到的內容摘錄，列出下一輪還需要查的 sub-query。
2. 下一輪的 sub-query 可以使用前面檢索到的具體資訊（例如人名、產品名、日期）。
3. 不要重複已經查過的 sub-query。
4. 如果現有內容已經足夠回答原始問題，回傳空清單。
5. sub-query 使用與原始問題相同的語言。
'''

multi_hop_decompose_user_prompt_template = '''
原始問題：{query}

已查過：
{hops}
'''

MULTI_HOP_DECOMPOSE_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", multi_hop_decompose_system_prompt_template),
        ("human", multi_hop_decompose_user_prompt_template),
    ]
)
