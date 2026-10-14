from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from app.db.session import get_db
from app.models import Tenant, User
from app.models.tenant import TenantStatus
from app.schemas import UserCreate, UserResponse, UserUpdate, UserLogin
from app.services import jwtService, getCurrentUser, ensureTenantActive
import bcrypt

router = APIRouter(tags=["Users"])

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


@router.post("/login", response_model=UserResponse)
async def login(loginModel: UserLogin, db: AsyncSession = Depends(get_db)):
    if not loginModel.email and not loginModel.username:
        raise HTTPException(status_code=500, detail="Username/Email is required")

    if loginModel.email:
        stmt = select(User).where(User.email == loginModel.email)
    elif loginModel.username:
        # username is only unique within a tenant, so it needs the tenant to pick one user.
        if not loginModel.tenant_slug:
            raise HTTPException(status_code=400, detail="tenant_slug is required when logging in with username")
        stmt = (
            select(User)
            .join(Tenant, Tenant.id == User.tenant_id)
            .where(User.username == loginModel.username, Tenant.slug == loginModel.tenant_slug)
        )
    exist_user = (await db.execute(stmt)).scalars().first()
    if not exist_user:
        raise HTTPException(status_code=404, detail="User not found")

    if bcrypt.checkpw(loginModel.password.encode('utf-8'), exist_user.hashed_password.encode('utf-8')):
        await ensureTenantActive(db, exist_user.tenant_id)
        payload = {"email": exist_user.email, "username": exist_user.username}
        token = jwtService.createToken(payload)
        pydantic_user = UserResponse.model_validate(exist_user)
        pydantic_user.token = token
        return pydantic_user
    else:
        raise HTTPException(status_code=500, detail="wrong username/password")

@router.post("/register", response_model=UserResponse)
async def register(user: UserCreate, db: AsyncSession = Depends(get_db)):
    try:
        stmt = select(User).where(User.email == user.email)
        existing_user = (await db.execute(stmt)).scalars().first()
        if existing_user:
            raise HTTPException(status_code=400, detail="Email already registered")
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc

    tenant_stmt = select(Tenant).where(
        Tenant.slug == user.tenant_slug,
        Tenant.status == TenantStatus.ACTIVE,
        Tenant.deleted_at.is_(None),
    )
    tenant = (await db.execute(tenant_stmt)).scalars().first()
    if not tenant:
        raise HTTPException(status_code=400, detail="Unknown or inactive tenant")

    username_stmt = select(User.id).where(User.tenant_id == tenant.id, User.username == user.username)
    if (await db.execute(username_stmt)).first():
        raise HTTPException(status_code=400, detail="Username already taken in this tenant")

    hashed_pw = hash_password(user.password)
    db_user = User(
        tenant_id=tenant.id,
        email=user.email,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        hashed_password=hashed_pw
    )
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)

    token = jwtService.createToken({"email": user.email, "username": user.username})
    user_pydantic = UserResponse.model_validate(db_user)
    user_pydantic.token = token
    return user_pydantic

@router.get("/")
async def getInfo(currUser: UserResponse = Depends(getCurrentUser)):
    print(currUser)
    return {"message": "Hello from HK AI Backend"}


def ensure_self(user_id: UUID, currUser: User) -> None:
    # Account changes are self-service only; without this any logged-in user could
    # rewrite another user's (or another tenant's) email/password by sending their id.
    if user_id != currUser.id:
        raise HTTPException(status_code=403, detail="Not allowed to modify another user")


@router.get("/user/{user_id}", response_model=UserResponse)
async def get_user(user_id: UUID, db: AsyncSession = Depends(get_db), currUser: User = Depends(getCurrentUser)):
    stmt = select(User).where(User.id == user_id, User.tenant_id == currUser.tenant_id)
    user = (await db.execute(stmt)).scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/users", response_model=list[UserResponse])
async def get_users(db: AsyncSession = Depends(get_db), currUser: User = Depends(getCurrentUser)):
    users = (await db.execute(select(User).where(User.tenant_id == currUser.tenant_id))).scalars().all()
    return users


@router.post("/update", response_model=UserResponse)
async def update_user(user: UserUpdate, db: AsyncSession = Depends(get_db), currUser: User = Depends(getCurrentUser)):
    ensure_self(user.id, currUser)
    exist_user = (await db.execute(select(User).filter(User.id == user.id))).scalars().first()
    if not exist_user:
        raise HTTPException(status_code=404, detail="User not found")
    exist_user.first_name = user.first_name
    exist_user.last_name = user.last_name
    exist_user.full_name = exist_user.first_name + " " + exist_user.last_name
    username_stmt = select(User.id).where(
        User.tenant_id == exist_user.tenant_id, User.username == user.username, User.id != exist_user.id
    )
    if (await db.execute(username_stmt)).first():
        raise HTTPException(status_code=400, detail="Username already taken in this tenant")
    exist_user.username = user.username
    await db.commit()
    return exist_user

@router.post("/email", response_model=UserResponse)
async def change_email(user: UserUpdate, db: AsyncSession = Depends(get_db), currUser: User = Depends(getCurrentUser)):
    ensure_self(user.id, currUser)
    exist_user = (await db.execute(select(User).where(User.id == user.id))).scalars().first()
    if not exist_user:
        raise HTTPException(status_code=404, detail="User not found")
    check_email = (await db.execute(select(User).where(User.id != user.id, User.email == user.email))).scalars().first()
    if check_email:
        raise HTTPException(status_code=500, detail="Used email")
    exist_user.email = user.email
    await db.commit()
    return exist_user


@router.post("/password", response_model=UserResponse)
async def change_password(user: UserUpdate, db: AsyncSession = Depends(get_db), currUser: User = Depends(getCurrentUser)):
    ensure_self(user.id, currUser)
    exist_user = (await db.execute(select(User).filter(User.id == user.id))).scalars().first()
    if not exist_user:
        raise HTTPException(status_code=404, detail="User not found")
    hashed_pw = hash_password(user.password)
    exist_user.hashed_password = hashed_pw
    await db.commit()
    return exist_user
