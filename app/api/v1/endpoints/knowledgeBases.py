from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from app.db.session import get_db
from app.models import User
from app.models.asistantKnowledgeBase import AsistantKnowledgeBase
from app.rag.loaders import base_loader
from app.rag.pipelines import create_pipeline
from app.rag.splitters.recursive_splitters import get_recursive_chunks
from app.schemas import DocumentUploadRequest, DocumentUploadResponse
from app.schemas.knowledgeBases import KnowledgeBaseCreateRequest, KnowledgeBaseCreateResponse, QueryAsistantRequest, QueryAsistantResponse
from app.services import jwtService, getCurrentUser
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
  except SQLAlchemyError as exc:
    raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
  except Exception as exc:
    raise HTTPException(status_code=500, detail=f"Error: {exc}") from exc
     
  
  kb_pydantic = KnowledgeBaseCreateResponse.model_validate(new_kb)
  return kb_pydantic

@router.post("/uploadDocument", response_model=DocumentUploadResponse)
async def uploadDocument(requestModel: DocumentUploadRequest, db:Session = Depends(get_db), current_user: User = Depends(getCurrentUser)):
  # validation by pydantic

  # check bytes
  # check extension, inside [pdf,docx]
  file = requestModel.file
  metadata = {
    "knowledge_base_id": requestModel.knowledge_base_id
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

  chunking_list = get_recursive_chunks(document_list)
  store = get_vector_store_indexer()
  store.add_documents(chunking_list, user_id=str(current_user.id), metadata=metadata)
  response = DocumentUploadResponse(success=True, message="upload success")
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
  



