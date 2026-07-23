from abc import ABC, abstractmethod

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

class BaseChunker(ABC):
    @abstractmethod
    def chunk(self, documents: list[Document]) -> list[Document]:
        pass

class RecursiveChunks(BaseChunker):
    def chunk(self, documents: list[Document]) -> list[Document]: # return: list of document
        splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=100, separators=["\n\n", "\n", " ", ""])
        return splitter.split_documents(documents)

