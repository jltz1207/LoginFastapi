from io import BytesIO
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.models import ChromaQueryRequest, ChromaQueryResponse, ChromaUpsertRequest, UserResponse
from app.services import getCurrentUser
from app.services.chroma_service import get_chroma_service
from pypdf import PdfReader
router = APIRouter(tags=["AI"])


@router.get("/health")
async def chroma_health(currUser: UserResponse = Depends(getCurrentUser)):
    _ = currUser
    return {"status": "ok", "message": "Chroma route is available"}


def split_text(text: str, chunk_size: int = 900, overlap: int = 120) -> list[str]:
    if not text.strip():
        return []

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == len(text):
            break
        start = max(0, end - overlap)
    return chunks


@router.post("/upload_pdf")
async def upload_pdf(
    doc: UploadFile = File(...),
    currUser: UserResponse = Depends(getCurrentUser),
):
    if not doc.filename or not doc.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only .pdf files are supported")

    try:
        file_bytes = await doc.read()
        if not file_bytes:
            raise HTTPException(status_code=400, detail="Uploaded PDF is empty")

        reader = PdfReader(BytesIO(file_bytes))
        documents: list[str] = []
        ids: list[str] = []
        metadatas: list[dict[str, str | int]] = []

        for page_idx, page in enumerate(reader.pages):
            page_text = (page.extract_text() or "").strip()
            if not page_text:
                continue
            chunks = split_text(page_text)
            for chunk_idx, chunk in enumerate(chunks):
                documents.append(chunk)
                ids.append(str(uuid4()))
                metadatas.append(
                    {
                        "file_name": doc.filename,
                        "owner_email": currUser.email,
                        "page": page_idx + 1,
                        "chunk": chunk_idx,
                    }
                )

        if not documents:
            raise HTTPException(status_code=400, detail="No readable text found in this PDF")

        service = get_chroma_service()
        service.upsert(ids=ids, documents=documents, metadatas=metadatas)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF ingestion failed: {exc}") from exc

    return {
        "message": "PDF uploaded and indexed in Chroma",
        "file_name": doc.filename,
        "chunks": len(documents),
    }


@router.post("/upsert")
async def upsert_documents(
    payload: ChromaUpsertRequest,
    currUser: UserResponse = Depends(getCurrentUser),
):
    _ = currUser
    try:
        service = get_chroma_service()
        service.upsert(ids=payload.ids, documents=payload.documents, metadatas=payload.metadatas)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Chroma upsert failed: {exc}") from exc

    return {"message": "Documents upserted", "count": len(payload.ids)}


@router.post("/query", response_model=ChromaQueryResponse)
async def query_documents(
    payload: ChromaQueryRequest,
    currUser: UserResponse = Depends(getCurrentUser),
):
    _ = currUser
    try:
        service = get_chroma_service()
        result = service.query(
            query_text=payload.query_text,
            n_results=payload.n_results,
            where=payload.where,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Chroma query failed: {exc}") from exc

    return ChromaQueryResponse(
        ids=result.get("ids", []),
        documents=result.get("documents"),
        metadatas=result.get("metadatas"),
        distances=result.get("distances"),
    )
