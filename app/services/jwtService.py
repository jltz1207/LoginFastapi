from app.core import settings
from datetime import datetime, timedelta, timezone
from jose import jwt
from fastapi.security import OAuth2PasswordBearer
from fastapi import Depends, HTTPException, status
from app.db import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import Tenant, User
from app.models.tenant import TenantStatus

oauth2Scheme = OAuth2PasswordBearer(tokenUrl="token")
class JwtService:
  @staticmethod
  def createToken(payload:dict):
    to_encode = payload.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

jwtService = JwtService()

async def getCurrentUser(token: str = Depends(oauth2Scheme), db: AsyncSession = Depends(get_db)):
  credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
  try:
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=settings.ALGORITHM)
    payloadEmail = payload.get("email")
    if not payloadEmail:
      raise credentials_exception
  except:
    raise credentials_exception

  user = (await db.execute(select(User).where(User.email == payloadEmail))).scalars().first()
  if not user:
    raise credentials_exception
  # Checked on every request, not only at login: a token issued before the tenant was
  # suspended must stop working too.
  await ensureTenantActive(db, user.tenant_id)

  return user

async def ensureTenantActive(db: AsyncSession, tenant_id) -> None:
  stmt = select(Tenant.id).where(
    Tenant.id == tenant_id,
    Tenant.status == TenantStatus.ACTIVE,
    Tenant.deleted_at.is_(None),
  )
  if (await db.execute(stmt)).first() is None:
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant is not active")
