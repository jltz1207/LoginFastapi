from langchain_core.documents import Document

def format_doc_to_string(docs: list[Document]):
    output_str = "\n".join(doc.page_content for doc in docs)
    return output_str
