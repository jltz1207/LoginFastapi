from langchain_text_splitters import RecursiveCharacterTextSplitter

def get_recursive_chunks(documents): # return: list of document
    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    return splitter.split_documents(documents)
