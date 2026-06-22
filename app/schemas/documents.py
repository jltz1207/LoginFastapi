from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel
from fastapi import UploadFile

from app.models.document import IngestionStatus

class DocumentResponse(BaseModel):
  id:UUID
  knowledge_base_id: UUID
  user_id: UUID
  filename: str
  file_extension: str
  file_size_bytes: int
  mime_type: str
  status: IngestionStatus
  page_count: int
  created_dt: Optional[datetime]
  created_by: Optional[str]
  modified_dt: Optional[datetime]
  modified_by: Optional[str]
  class Config:
        from_attributes = True  # Allows conversion from SQLAlchemy models

class DocumentUploadRequest(BaseModel):
  file: UploadFile
  knowledge_base_id: UUID

class DocumentUploadResponse(BaseModel):
  success:bool
  message:str
  document: DocumentResponse

