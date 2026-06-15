from app.core import settings
from datetime import datetime, timedelta, timezone
from jose import jwt
from fastapi.security import OAuth2PasswordBearer
from fastapi import Depends, HTTPException, status
from app.db import get_db
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.models import User

oauth2Scheme = OAuth2PasswordBearer(tokenUrl="token")
class JwtService:
  @staticmethod # allow JwtService.createToken(data) (Calling it like a static utility)
  def createToken(payload:dict):
    to_encode = payload.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
  
jwtService = JwtService()  # allow jwt_service.createToken(data) (Using your instance)

def getCurrentUser(token:str = Depends(oauth2Scheme), db:Session = Depends(get_db)) :
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
  
  user = db.execute(select(User).where(User.email == payloadEmail)).scalars().first()
  if not user:
    raise credentials_exception
  
  return user