from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.db.session import Base

class Version(Base):
  __tablename__ = "versions"

  id = Column(Integer, primary_key=True, index=True)
  version = Column(String, nullable=False)
  created_at = Column(DateTime(timezone=True), server_default=func.now())
  updated_at = Column(DateTime(timezone=True), onupdate=func.now())