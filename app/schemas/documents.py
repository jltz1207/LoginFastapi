from uuid import UUID

from pydantic import BaseModel
from fastapi import UploadFile

class DocumentUploadRequest(BaseModel):
  file: UploadFile
  knowledge_base_id: UUID

class DocumentUploadResponse(BaseModel):
  success:bool
  message:str
