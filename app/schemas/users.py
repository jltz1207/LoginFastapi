from uuid import UUID

from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime

# a complete set of DTOs 

class UserBase(BaseModel):
    email: EmailStr
    username: str
    first_name: Optional[str] = None # can be none
    last_name: Optional[str] = None # can be none
    

# Model for creating a new user (request body)
class UserCreate(UserBase):
    password: str

# Model for updating user info (request body)
class UserUpdate(BaseModel):
    id: UUID
    email: Optional[EmailStr] = None
    username: Optional[str] = None
    first_name: Optional[str] = None # can be none
    last_name: Optional[str] = None # can be none
    password: Optional[str] = None

# Model for user login (request body)
class UserLogin(BaseModel):
    email: Optional[EmailStr] = None
    username: Optional[str] = None
    password: str

# Model for user response (API response)
class UserResponse(BaseModel):
    id: UUID
    email: EmailStr
    username: str
    is_active: bool
    created_at: datetime
    token: Optional[str] = None
    class Config:
        from_attributes = True  # Allows conversion from SQLAlchemy models

