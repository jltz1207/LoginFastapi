import asyncio
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from app.core.storage.fileStorage import delete_file, upload_file
from app.ingestion.worker import notify_ingestion_worker
from app.db.session import get_db
from app.models import User
from app.models.asistantKnowledgeBase import AsistantKnowledgeBase
from app.models.document import Document, IngestionStatus
from app.rag.loaders.base_loader import base_loader
from app.rag.pipelines import create_pipeline
from app.schemas import  DocumentUploadResponse
from app.schemas.documents import DocumentResponse
from app.schemas.knowledgeBases import KnowledgeBaseCreateRequest, KnowledgeBaseCreateResponse, QueryAsistantRequest, QueryAsistantResponse
from app.services import jwtService, getCurrentUser
from app.services.knowledgeBaseService import get_owned_knowledge_base
from fastapi import UploadFile

router = APIRouter(tags=["KnowledgeBases"])


@router.post("/create", response_model=KnowledgeBaseCreateResponse)
async def createKnowledgeBase(requestModel: KnowledgeBaseCreateRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(getCurrentUser)):

  try:
    trim_title = requestModel.title.strip()
    get_title_stmt = select(AsistantKnowledgeBase).where(AsistantKnowledgeBase.user_id == current_user.id, AsistantKnowledgeBase.title == trim_title)
    exist_title = (await db.execute(get_title_stmt)).scalars().all()
    if exist_title:
      raise HTTPException(status_code=500, detail=f"Title '{trim_title}' is used.")
    new_kb = AsistantKnowledgeBase(
      user_id=current_user.id,
      tenant_id=current_user.tenant_id,
      title=requestModel.title,
    )
    db.add(new_kb)
    await db.commit()
    await db.refresh(new_kb)
    kb_pydantic = KnowledgeBaseCreateResponse.model_validate(new_kb)
  except SQLAlchemyError as exc:
    raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
  except Exception as exc:
    raise HTTPException(status_code=500, detail=f"Error: {exc}") from exc
     
  
  
  return kb_pydantic

@router.post("/upload", response_model=DocumentUploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def uploadDocument(file: UploadFile = File(...), knowledge_base_id: str = Form(...), db: AsyncSession = Depends(get_db), current_user: User = Depends(getCurrentUser)):
  # Only validate, store the file and queue it: load / chunk / embed / summary run in the
  # ingestion worker (app/ingestion/), which picks up the PENDING row.
  # Outside the try: its `except Exception` would turn this 404 into a 500.
  kb = await get_owned_knowledge_base(db, knowledge_base_id, current_user)
  stored = None
  try:
    file_ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if file_ext not in base_loader.SUPPORTED_EXTENSIONS:
      raise HTTPException(status_code=400, detail="File extension not allowed.")

    file_bytes = await file.read()
    if len(file_bytes) > 10 * 1024 * 1024: # 10 MB
      raise HTTPException(status_code=400, detail="File size is too large. Limit: 10MB.")

    # Stored before the row exists: the worker reads the file back from storage.
    stored = await asyncio.to_thread(
      upload_file, current_user.tenant_id, current_user.id, kb.id, file_bytes, file.filename, file.content_type
    )
    storage_bucket = stored.get("storage_bucket")
    storage_key = stored.get("storage_key")
    if not storage_bucket or not storage_key:
      raise HTTPException(
        status_code=500,
        detail="File upload failed: storage location could not be determined",
    )

    new_doc = Document(
      knowledge_base_id = kb.id,
      user_id = current_user.id,
      tenant_id = current_user.tenant_id,
      filename = Path(file.filename).name,
      file_extension = file_ext,
      mime_type = file.content_type,
      file_size_bytes = len(file_bytes),
      storage_bucket = storage_bucket,
      storage_key = storage_key,
      status = IngestionStatus.PENDING,
    )
    db.add(new_doc)
    await db.commit()
    stored = None  # the row owns the file now
    await db.refresh(new_doc)
    notify_ingestion_worker()
    response_doc = DocumentResponse.model_validate(new_doc)
    response = DocumentUploadResponse(success=True, message="upload accepted", document=response_doc)
  except HTTPException:
    raise
  except SQLAlchemyError as exc:
    raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
  except Exception as exc:
    raise HTTPException(status_code=500, detail=f"Error: {exc}") from exc
  finally:
    if stored and stored.get("storage_key"):
      # No row points at the file, so nothing would ever read or delete it.
      await asyncio.to_thread(delete_file, stored.get("storage_bucket"), stored["storage_key"])

  return response


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def getDocument(document_id: UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(getCurrentUser)):
  # Ingestion status lookup. Scoped to the caller; 404 rather than 403 so a foreign id
  # does not reveal that it exists.
  stmt = select(Document).where(
    Document.id == document_id,
    Document.tenant_id == current_user.tenant_id,
    Document.user_id == current_user.id,
    Document.deleted_at.is_(None),
  )
  doc = (await db.execute(stmt)).scalars().first()
  if doc is None:
    raise HTTPException(status_code=404, detail="Document not found.")
  return DocumentResponse.model_validate(doc)


@router.post("/query", response_model=QueryAsistantResponse)
async def query(model: QueryAsistantRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(getCurrentUser)):

    await get_owned_knowledge_base(db, model.knoweldge_base_id, current_user)
    pipeline_runnable = create_pipeline(
        tenant_id=str(current_user.tenant_id),
        user_id=str(current_user.id),
        knowledge_base_id=str(model.knoweldge_base_id),
    )
    input_state = {
        "question": model.question,
        "chat_history": []
    }
    output_state = pipeline_runnable.invoke(input_state)
    return QueryAsistantResponse(answer=output_state.answer, source=output_state.source)
  



