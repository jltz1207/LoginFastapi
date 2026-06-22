from typing import Literal
from langchain_core.documents import Document
from app.rag.loaders.md_loader import md_parser_from_bytes
from app.rag.loaders.pdf_loader import pdf_parser_from_bytes

class base_loader:
    @staticmethod
    def load(file_ext:str , file_bytes:bytes):
        if file_ext.strip() ==  "pdf":
            return pdf_parser_from_bytes(file_bytes)
        elif file_ext.strip() == "md":
            return md_parser_from_bytes(file_bytes)
        else:
            return None
