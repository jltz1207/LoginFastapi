from langchain_core.documents import Document

from app.rag.splitters.chunkers import RecursiveChunks


class ChunkerFactory():
    registry = {
        "recursive":  RecursiveChunks
    }
    def create_chunker(cls, strategy_name:str):
        chunk_class = cls.registry.get(strategy_name.lower(), "recursive")
        if not chunk_class:
            raise ValueError("Unknown input strategy_name")
        return chunk_class()
    
    def print_chunks_transformation(cls, pre_doc_list: list[Document], aft_doc_list: list[Document]):
        print("Loader:")
        for document in pre_doc_list:
            print(document.page_content)
        print("-----------------------------------")
        print("After splitter:")
        for idx, document in enumerate(aft_doc_list, start=1):
            content = document.page_content
            print(f"Chunks {idx}: (chunk_size: {len(content)})")
            print(content)
        print("-----------------------------------")