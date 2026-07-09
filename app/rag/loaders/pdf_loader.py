from langchain_community.document_loaders import PyMuPDFLoader, PyPDFLoader
from langchain_community.document_loaders.parsers import PyMuPDFParser, PyPDFParser
from langchain_core.document_loaders.blob_loaders import Blob
import tempfile

def pdf_loader(path:str): # from path
    loader = PyPDFLoader(path)
    documents = loader.load()
    return documents

def pdf_parser_from_bytes(pdf_bytes: bytes):
    blob = Blob.from_data(data=pdf_bytes, mime_type="application/pdf")
    # parser = PyPDFParser()
    parser = PyMuPDFParser()
    documents = parser.parse(blob)
    return documents

def pdf_parser_from_bytes_(pdf_bytes: bytes):
    with tempfile.NamedTemporaryFile(mode="wb", delete=True, suffix=".pdf") as temp_pdf:
        temp_pdf.write(pdf_bytes)
        temp_pdf.flush()  # Force write the buffer
        return pdf_loader(temp_pdf.name)