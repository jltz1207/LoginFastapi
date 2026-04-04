from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.db.session import get_db
from app.models import User
from app.schemas import UserCreate, UserResponse, UserUpdate, UserLogin
from app.services import jwtService, getCurrentUser
import bcrypt

router = APIRouter(tags=["Users"])

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


@router.post("/login", response_model = UserResponse)
async def login(loginModel: UserLogin, db:Session = Depends(get_db)):
    # check both empty
    if not loginModel.email and not loginModel.username:
        raise HTTPException(status_code = 500, detail="Username/Email is required")
    
    # check existence
    if loginModel.email: 
        stmt = select(User).where(User.email == loginModel.email)
    elif loginModel.username:
        stmt = select(User).where(User.username == loginModel.username)
    exist_user = db.execute(stmt).scalars().first()
    if not exist_user:
        raise HTTPException(status_code = 404, detail ="User not found")
    
    # check password
    if bcrypt.checkpw(loginModel.password.encode('utf-8'), exist_user.hashed_password.encode('utf-8')):
        payload = {"email": exist_user.email, "username": exist_user.username }
        token =  jwtService.createToken(payload)
        pydantic_user = UserResponse.model_validate(exist_user)
        pydantic_user.token = token
        return pydantic_user
    else:
        raise HTTPException(status_code = 500, detail ="wrong username/password")

@router.post("/register", response_model=UserResponse)
async def register(user: UserCreate, db: Session = Depends(get_db)):
    # Check if user already exists
    stmt = select(User).where(User.email == user.email)
    existing_user = db.execute(stmt).scalars().first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Hash the password
    hashed_pw = hash_password(user.password)
    
    # Create new user
    db_user = User(
        email= user.email,
        username = user.username,
        first_name= user.first_name,
        last_name= user.last_name,
        full_name = user.first_name + " " + user.last_name,
        hashed_password=hashed_pw
    )
    # Save to database
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    
    token = jwtService.createToken({"email": user.email, "username": user.username})
    user_pydantic = UserResponse.model_validate(db_user)
    user_pydantic.token = token

    
    
    return user_pydantic
   
@router.get("/")
async def getInfo( currUser: UserResponse = Depends(getCurrentUser)):
    print(currUser)
    return {"message": "Hello from HK AI Backend"}



@router.get("/user/{user_id}", response_model=UserResponse)
async def get_user(user_id: int, db: Session = Depends(get_db), currUser: UserResponse = Depends(getCurrentUser)):
    user = db.execute(select(User).where(User.id == user_id))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user 


@router.get("/users", response_model = list[UserResponse])
async def get_users(db:Session = Depends(get_db), currUser: UserResponse = Depends(getCurrentUser)):
    users = db.execute(select(User)).scalars().all() # to list[Users]
    return users


@router.post("/update", response_model = UserResponse)
async def update_user(user:UserUpdate, db:Session = Depends(get_db), currUser: UserResponse = Depends(getCurrentUser)):
    exist_user = db.execute(select(User).filter(User.id == user.id)).scalars().first()
    if not exist_user:
        raise HTTPException(status_code = 404, detail="User not found")
    # change profile field
    exist_user.first_name = user.first_name
    exist_user.last_name = user.last_name
    exist_user.full_name = exist_user.first_name + " " + exist_user.last_name
    exist_user.username = user.username
    db.commit()
    return exist_user

@router.post("/email", response_model = UserResponse)
async def change_email(user:UserUpdate, db:Session = Depends(get_db), currUser: UserResponse = Depends(getCurrentUser)):
    exist_user = db.execute(select(User).where(User.id == user.id)).scalars().first()
    if not exist_user:
        raise HTTPException(status_code = 404, detail="User not found")
    check_email = db.execute(select(User).where(User.id != user.id, User.email == user.email)).scalars().first()
    if check_email:
        raise HTTPException(status_code = 500, detail="Used email")
    exist_user.email = user.email
    db.commit()
    return exist_user
    

    

@router.post("/password", response_model = UserResponse)
async def change_password(user:UserUpdate, db:Session = Depends(get_db), currUser: UserResponse = Depends(getCurrentUser)):
    exist_user = db.execute(select(User).filter(User.id == user.id)).scalars().first()
    if not exist_user:
        raise HTTPException(status_code = 404, detail="User not found")
    # change password
    hashed_pw = hash_password(user.password)
    exist_user.hashed_password = hashed_pw
    db.commit()
    return exist_user

