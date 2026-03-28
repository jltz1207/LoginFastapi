from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime

# a complete set of DTOs 

class UserBase(BaseModel):
    email: EmailStr
    firstName: Optional[str] = None # can be none
    lastName: Optional[str] = None # can be none

# Model for creating a new user (request body)
class UserCreate(UserBase):
    password: str

# Model for updating user info (request body)
class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    password: Optional[str] = None

# Model for user login (request body)
class UserLogin(BaseModel):
    email: EmailStr
    password: str

# Model for user response (API response)
class UserResponse(BaseModel):
    id: int
    email: EmailStr
    full_name: Optional[str] = None
    is_active: bool = True
    created_at: datetime

    class Config:
        from_attributes = True  # Allows conversion from SQLAlchemy models

