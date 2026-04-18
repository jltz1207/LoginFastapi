from fastapi import APIRouter, Depends, HTTPException

from app.schemas import ChromaQueryRequest, ChromaQueryResponse, ChromaUpsertRequest, UserResponse
from app.services import getCurrentUser
from app.services.chroma_service import get_chroma_service

router = APIRouter(tags=["AI"])


@router.get("/health")
async def chroma_health(currUser: UserResponse = Depends(getCurrentUser)):
    _ = currUser
    return {"status": "ok", "message": "Chroma route is available"}


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
