from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

def get_recursive_chunks(documents: list[Document]): # return: list of document
    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=200, separators=["\n\n", "\n", " ", ""])
    return splitter.split_documents(documents)
