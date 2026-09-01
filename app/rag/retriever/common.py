from app.vectorstore.StoreIndexer import get_vector_store_indexer
from langchain_core.documents import Document

def  get_collection_retriever(tenant_id, user_id, knowledge_base_id, *, top_k:int=8, search_type: str = "similarity",  extra_filter: dict | None = None):
    indexer = get_vector_store_indexer()
    collection = indexer.collection_manager.get_or_create_collection(user_id=user_id, tenant_id=tenant_id)
    where_conditions = [
        {"knowledge_base_id": knowledge_base_id},
        {"user_id": user_id},
        {"tenant_id": tenant_id}
    ]
    if extra_filter:
        where_conditions.append(extra_filter)
    where: dict = {"$and": where_conditions}
    
    return collection.as_retriever(
        search_type=search_type,
        search_kwargs={
            "k": top_k,
            "filter": where
        }
    )

def format_doc_to_string(docs: list[Document]):
    output_str = "\n".join(doc.page_content for doc in docs)
    return output_str