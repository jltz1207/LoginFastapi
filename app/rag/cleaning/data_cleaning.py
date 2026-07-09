import re
from langchain_core.documents import Document

def clean_extracted_documents(docs: list[Document]) -> list[Document]:
    cleaned_docs = []
    
    for doc in docs:
        text = doc.page_content
        
        # 1. Normalize all varied whitespace characters (tabs, non-breaking spaces) to regular spaces
        text = re.sub(r'[\t\r \xa0]+', ' ', text)
        
        # 2. Fix broken sentences that were split across pages/lines by a lone newline
        # If a line ends with a lowercase letter, comma, or word and is followed by a newline, 
        # replace it with a space instead of a line break.
        text = re.sub(r'(?<=[a-zA-Z,])\n(?=[a-zA-Z])', ' ', text)
        
        # 3. Ensure true paragraphs (ending in ., !, or ?) get a clean double newline spacer
        text = re.sub(r'(?<=[.!?])\s*\n+\s*', '\n\n', text)
        
        # 4. Clean up any accidental triple+ newlines caused by step 3
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        # 5. Strip any leftover ugly padding from the edges
        text = text.strip()
        
        # Only keep the document if it actually contains text after cleaning
        if text:
            doc.page_content = text
            cleaned_docs.append(doc)
            
    return cleaned_docs

def clean_chunks(chunks:list[Document]):
    cleaned_chunks = []
    for chunk in chunks:
        text = chunk.page_content
        
        # Replace single newlines with spaces, but don't break hyphenated words
        # This converts "hello\nworld" to "hello world"
        text = re.sub(r'(?<!\n)\n(?!\n)', ' ', text)
        
        # Collapse any accidental double spaces created by the replacement
        text = re.sub(r' +', ' ', text)
        
        # Strip trailing/leading spaces from the chunk edges
        text = text.strip()
        
        # Update the chunk with the clean text
        if text:
            chunk.page_content = text
            cleaned_chunks.append(chunk)
            
    return cleaned_chunks