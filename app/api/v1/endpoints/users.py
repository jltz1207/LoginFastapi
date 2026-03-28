from fastapi import APIRouter
from app.schemas.users import UserCreate, UserResponse
import json

router = APIRouter(tags=["Users"])

@router.get("/")
async def getInfo():
    return {"message": "Hello from HK AI Backend"}

@router.post("/register", response_model=UserResponse)
async def register(registerModel:UserCreate):
    new_user = {"id":1, **registerModel.model_dump()} # dict
    
    # handles dict to db model
    
    # json_user = json.dumps(new_user) # py dict to json 
    return 
