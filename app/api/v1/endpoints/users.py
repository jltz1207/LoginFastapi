from fastapi import APIRouter

router = APIRouter(tags=["Users"])

@router.get("/")
async def get_users():
    return {"message": "Hello from HK AI Backend"}

@router.get("/info")
async def getInfo(
    id:int = 0,
    value:str | None = None
):
    return {"id": id, "value": value}   