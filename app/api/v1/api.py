from fastapi import APIRouter, Depends, HTTPException
from app.api.v1.endpoints import users, documents, chats
from sqlalchemy.orm import Session
from app.db.session import get_db
from sqlalchemy import select
from app.models import Version
from app.schemas import VersionResponse
from app.utils import get_ip
api_router = APIRouter()
api_router.include_router(users.router, prefix="/users", tags=["Users"])
api_router.include_router(documents.router, prefix="/documents", tags=["Documents"])

@api_router.get("/healthcheck")
async def healthcheck(db:Session = Depends(get_db)):
  try:
    stmt = select(Version).order_by(Version.id.desc())
    ver = db.execute(stmt).scalars().first()
    if ver: 
      ver_schema = VersionResponse.model_validate(ver) 
      ip = get_ip()
      ver_dict = ver_schema.model_dump()
      ver_dict["ip"] = ip
      return ver_dict
    else:
      raise HTTPException(status_code=404, detail="Version not found")
    return ver_schema
  except Exception as e:
    raise HTTPException(status_code=500, detail=f"Server Error: {e}")

