from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from app.db.session import get_db
from app.models import User
from app.schemas import DocumentUploadRequest, DocumentUploadResponse
from app.services import jwtService, getCurrentUser
from app.ingestion import base_loader, get_recursive_chunks
from app.vectorstore import get_vector_store_indexer
router = APIRouter(tags=["Documents"])
from fastapi import UploadFile

@router.post("/uploadDocument", response_model=DocumentUploadResponse)
async def uploadDocument(file: UploadFile, db:Session = Depends(get_db)):
  # validation by pydantic

  # check bytes
  # check extension, inside [pdf,docx]

  allowed_extension = ["pdf", "md", "docx"]
  file_ext = file.filename.split(".")[1]
  if file_ext not in allowed_extension:
    return HTTPException(status_code=400, details="File extension not allowed.")
  
  file_bytes = await file.read()
  if len(file_bytes) > 10 * 1024 * 1024: # 10 MB
    return HTTPException(status_code=400, details="File size is too large. Limit: 10MB.")

  store = get_vector_store_indexer()  
  document_list = base_loader(file_ext, file_bytes)
  chunking_list = get_recursive_chunks(document_list)
  store.add_documents(chunking_list)
  response = DocumentUploadResponse(success=True, message="upload success")
  return response

  
  
  



