from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from app.db.session import get_db
from app.models import User
from app.schemas import DocumentUploadRequest, DocumentUploadResponse
from app.services import jwtService, getCurrentUser

router = APIRouter(tags=["Documents"])

@router.post("/uploadDocument", response_model=DocumentUploadResponse)
async def uploadDocument(uploadModel:DocumentUploadRequest, db:Session = Depends(get_db)):
  # validation by pydantic

  # check bytes
  # check extension, inside [pdf,docx]

  allowed_extension = ["pdf", "doc", "docx"]
  file_ext = uploadModel.file.filename.split(".")[1]
  if file_ext not in allowed_extension:
    return HTTPException(status_code=400, details="File extension not allowed.")
  
  content = await uploadModel.file.read()
  if len(content) > 10 * 1024 * 1024: # 10 MB
    return HTTPException(status_code=400, details="File size is too large. Limit: 10MB.")
  
  
  



