from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr

from app.models.user import UserRole


class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str
    phone_number: Optional[str] = None
    role: UserRole


class UserRoleUpdate(BaseModel):
    role: UserRole


class UserResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    email: str
    name: str
    phone_number: Optional[str]
    role: UserRole
    is_approved: bool = False
    is_frozen: bool = False
    created_at: Optional[datetime] = None
