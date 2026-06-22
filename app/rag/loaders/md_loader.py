from langchain_community.document_loaders import UnstructuredMarkdownLoader
import tempfile

def md_loader(path:str): # from path
    loader = UnstructuredMarkdownLoader(path)
    documents = loader.load()
    return documents

def md_parser_from_bytes(md_bytes: bytes):
    with tempfile.NamedTemporaryFile(mode="wb", delete=True, suffix=".md") as temp_md:
        temp_md.write(md_bytes)
        temp_md.flush()
        return md_loader(temp_md.name)


