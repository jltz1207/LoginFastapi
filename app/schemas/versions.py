from pydantic import BaseModel, ConfigDict
from datetime import datetime
class VersionResponse(BaseModel):
  id: int
  version: str
  created_at: datetime
  updated_at: datetime
  model_config = ConfigDict(from_attributes=True)