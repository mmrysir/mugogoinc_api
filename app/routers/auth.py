from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.database import get_db
from app.models.user import User, UserRole

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

router = APIRouter(tags=["Auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    name: str
    password: str
    phone_number: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/auth/register", response_model=dict)
async def register(payload: RegisterIn, db: AsyncSession = Depends(get_db)) -> dict:
    q = select(User).where(User.email == payload.email)
    existing = await db.scalar(q)
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    hashed = security.hash_password(payload.password)
    user = User(email=payload.email, name=payload.name, hashed_password=hashed, phone_number=payload.phone_number)
    db.add(user)
    await db.flush()
    return {"id": user.id, "email": user.email, "name": user.name, "phone_number": user.phone_number}


class LoginIn(BaseModel):
    email: EmailStr
    password: str


@router.post("/auth/login", response_model=TokenOut)
async def login(payload: LoginIn, db: AsyncSession = Depends(get_db)) -> Any:
    q = select(User).where(User.email == payload.email)
    user = await db.scalar(q)
    if not user or not security.verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = security.create_access_token({"sub": user.id, "role": user.role.value}, expires_delta=timedelta(hours=8))
    return {"access_token": token}


async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)) -> User:
    try:
        data = security.decode_access_token(token)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token")

    user_id = data.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")

    q = select(User).where(User.id == user_id)
    user = await db.scalar(q)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def require_role(*allowed: UserRole):
    """Dependency factory restricting an endpoint to specific roles."""

    async def dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient privileges",
            )
        return current_user

    return dependency


@router.get("/auth/me")
async def me(current_user: User = Depends(get_current_user)) -> dict:
    return {"id": current_user.id, "email": current_user.email, "name": current_user.name, "role": current_user.role}
