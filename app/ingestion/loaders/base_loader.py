from app.ingestion import  pdf_parser_from_bytes_, md_parser_from_bytes
from typing import Literal
from langchain_core.documents import Document

from app.ingestion.loaders.pdf_loader import pdf_parser_from_bytes

def base_loader(file_ext:str , file_bytes:bytes):
    if file_ext.strip() ==  "pdf":
        return pdf_parser_from_bytes(file_bytes)
    elif file_ext.strip() == "md":
        return md_parser_from_bytes(file_bytes)
    else:
        return None
