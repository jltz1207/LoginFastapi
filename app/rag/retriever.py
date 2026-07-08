from langchain_core.documents import Document
# from langchain_core.retrievers import BaseRetriever

from app.vectorstore import get_vector_store_indexer

def format_doc_to_string(docs: list[Document]):
    output_str = "\n".join(doc.page_content for doc in docs)
    return output_str

def  get_collection_retriever(user_id, search_type="similarity", search_kwags:dict={}):
    indexer = get_vector_store_indexer()
    collection = indexer.collections.get_or_create_collection(user_id)
    return collection.as_retriever(
        search_type=search_type,
        search_kwags=search_kwags
    )