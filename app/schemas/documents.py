from pydantic import BaseModel
from fastapi import UploadFile

class DocumentUploadRequest(BaseModel):
  file: UploadFile

class DocumentUploadResponse(BaseModel):
  success:bool
  message:str
