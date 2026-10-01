from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.database import get_db
from app.models.user import User, UserRole
from app.routers.auth import require_role
from app.schemas.user import UserCreate, UserResponse, UserRoleUpdate

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("", response_model=list[UserResponse])
async def list_users(
    role: Optional[UserRole] = Query(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.SUPER_ADMIN, UserRole.MANAGER)),
) -> list[User]:
    q = select(User).order_by(User.created_at.desc())
    if role:
        q = q.where(User.role == role)
    result = await db.execute(q)
    return list(result.scalars().all())


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.SUPER_ADMIN)),
) -> User:
    existing = await db.scalar(select(User).where(User.email == payload.email))
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    user = User(
        email=payload.email,
        name=payload.name,
        hashed_password=security.hash_password(payload.password),
        phone_number=payload.phone_number,
        role=payload.role,
        is_approved=True,  # Admin-created users are auto-approved
    )
    db.add(user)
    await db.flush()
    return user


@router.patch("/{user_id}/role", response_model=UserResponse)
async def update_user_role(
    user_id: str,
    payload: UserRoleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.SUPER_ADMIN)),
) -> User:
    user = await db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.id == current_user.id and payload.role != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot demote your own SUPER_ADMIN account",
        )

    user.role = payload.role
    await db.flush()
    return user


@router.patch("/{user_id}/approve", response_model=UserResponse)
async def approve_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.SUPER_ADMIN, UserRole.MANAGER)),
) -> User:
    """Approve a pending agent account request."""
    user = await db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.is_approved:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is already approved",
        )

    user.is_approved = True
    await db.flush()
    return user


@router.patch("/{user_id}/reject", response_model=dict)
async def reject_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.SUPER_ADMIN, UserRole.MANAGER)),
) -> dict:
    """Reject and delete a pending agent account request."""
    user = await db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.is_approved:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot reject an already approved user",
        )

    await db.delete(user)
    return {"message": f"User {user.email} has been rejected and removed."}