from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from app.core.storage.fileStorage import upload_file
from app.core.config import settings
from app.db.session import get_db
from app.models import User
from app.models.asistantKnowledgeBase import AsistantKnowledgeBase
from app.models.document import Document, IngestionStatus
from app.rag.loaders.base_loader import base_loader
from app.rag.pipelines import create_pipeline
from app.rag.splitters.recursive_splitters import get_recursive_chunks
from app.schemas import  DocumentUploadResponse
from app.schemas.documents import DocumentResponse
from app.schemas.knowledgeBases import KnowledgeBaseCreateRequest, KnowledgeBaseCreateResponse, QueryAsistantRequest, QueryAsistantResponse
from app.services import jwtService, getCurrentUser
from app.utils.token_counter import count_tokens
from app.vectorstore import get_vector_store_indexer
from fastapi import UploadFile

router = APIRouter(tags=["KnowledgeBases"])

@router.post("/create", response_model=KnowledgeBaseCreateResponse)
async def createKnowledgeBase(requestModel: KnowledgeBaseCreateRequest, db:Session = Depends(get_db), current_user: User = Depends(getCurrentUser)):
  
  try:
    trim_title = requestModel.title.strip()
    get_title_stmt = select(AsistantKnowledgeBase).where(AsistantKnowledgeBase.user_id == current_user.id, AsistantKnowledgeBase.title == trim_title)
    exist_title = db.execute(get_title_stmt).scalars().all()
    if exist_title:
      raise HTTPException(status_code=500, detail=f"Title '{trim_title}' is used.")
    new_kb = AsistantKnowledgeBase(
      user_id=current_user.id,
      title=requestModel.title,
    )
    db.add(new_kb)
    db.commit()
    db.refresh(new_kb)
    kb_pydantic = KnowledgeBaseCreateResponse.model_validate(new_kb)
  except SQLAlchemyError as exc:
    raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
  except Exception as exc:
    raise HTTPException(status_code=500, detail=f"Error: {exc}") from exc
     
  
  
  return kb_pydantic

@router.post("/upload", response_model=DocumentUploadResponse)
async def uploadDocument(file: UploadFile = File(...), knowledge_base_id: str = Form(...), db:Session = Depends(get_db), current_user: User = Depends(getCurrentUser)):
  # validation by pydantic

  # check bytes
  # check extension, inside [pdf,docx]
  try:
    metadata = {
      "knowledge_base_id": knowledge_base_id
    }
    allowed_extension = ["pdf", "md", "docx"]
    file_ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if file_ext not in allowed_extension:
      raise HTTPException(status_code=400, detail="File extension not allowed.")

    file_bytes = await file.read()
    if len(file_bytes) > 10 * 1024 * 1024: # 10 MB
      raise HTTPException(status_code=400, detail="File size is too large. Limit: 10MB.")

    document_list = base_loader.load(file_ext, file_bytes)
    if document_list is None:
      raise HTTPException(status_code=400, detail="Unsupported file type for processing.")
    page_count = len(document_list)
    chunking_list = get_recursive_chunks(document_list)
    chunk_count = len(chunking_list)
    chunking_list_str = [doc.page_content for doc in chunking_list]
    token_count = count_tokens(chunking_list_str, settings.EMBEDDING_PROVIDE_TYPE)
    store = get_vector_store_indexer()
    store.add_documents(chunking_list, user_id=current_user.id, metadata=metadata)
    
    # handle upload process
    result =  upload_file(current_user.id, knowledge_base_id, file_bytes, file.filename, file.content_type)
    storage_bucket = result.get("storage_bucket")
    storage_key = result.get("storage_key")
    if not storage_bucket or not storage_key:
      raise HTTPException(
        status_code=500,
        detail="File upload failed: storage location could not be determined",
    )

    # save to database
    new_doc = Document(
      knowledge_base_id=knowledge_base_id,
      user_id = current_user.id,
      filename = Path(file.filename).name,
      file_extension = file_ext,
      mime_type = file.content_type,
      file_size_bytes = len(file_bytes),
      storage_bucket = storage_bucket,
      storage_key = storage_key,
      status = IngestionStatus.INDEXED,
      chunk_count = chunk_count,
      token_count = token_count,
      raw_text = "\n.".join(chunking_list_str),
      page_count = page_count,
    )
    db.add(new_doc)
    db.commit()
    db.refresh(new_doc)
    response_doc = DocumentResponse.model_validate(new_doc)
    response = DocumentUploadResponse(success=True, message="upload success", document=response_doc)
  except SQLAlchemyError as exc:
    raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
  except Exception as exc:
    raise HTTPException(status_code=500, detail=f"Error: {exc}") from exc

  return response


@router.post("/query", response_model=QueryAsistantResponse)
async def query(model:QueryAsistantRequest, db:Session = Depends(get_db), current_user: User = Depends(getCurrentUser)):

    pipeline_runnable = create_pipeline(current_user.id)
    input_state = {
        "question": model.question,
        "chat_history": []
    }
    output_state = pipeline_runnable.invoke(input_state)
    return QueryAsistantResponse(answer=output_state.answer, source=output_state.source)
  



