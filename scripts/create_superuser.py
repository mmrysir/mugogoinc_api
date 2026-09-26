"""Create the initial SUPER_ADMIN user from the command line.

Usage:
    venv/bin/python scripts/create_superuser.py
    venv/bin/python scripts/create_superuser.py --email admin@mugogoinc.com

This replaces the old open ``POST /api/v1/auth/setup`` endpoint, removing
the risk of exposing a privileged bootstrap route to the internet.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

# Ensure the backend project root is importable when this script is run
# directly (e.g. `venv/bin/python scripts/create_superuser.py`).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import AsyncSessionLocal, create_all_tables
from app.models.user import User, UserRole
from passlib.context import CryptContext
from sqlalchemy import select

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")



async def create_superuser(email: str, name: str, password: str) -> None:
    await create_all_tables()

    async with AsyncSessionLocal() as session:
        existing = await session.scalar(select(User).where(User.email == email))
        if existing:
            print(f"✗ A user with email {email!r} already exists.", file=sys.stderr)
            sys.exit(1)

        user = User(
            email=email,
            name=name,
            hashed_password=_pwd_context.hash(password),
            phone_number=None,
            role=UserRole.SUPER_ADMIN,
        )
        session.add(user)
        await session.commit()

    print(f"✓ SUPER_ADMIN created: {email} ({name})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a SUPER_ADMIN user.")
    parser.add_argument("--email", help="Admin email address")
    parser.add_argument("--name", help="Admin display name")
    args = parser.parse_args()

    email = args.email or input("Email: ").strip()
    name = args.name or input("Name: ").strip()

    if not email or not name:
        print("Email and name are required.", file=sys.stderr)
        sys.exit(1)

    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")

    if password != confirm:
        print("Passwords do not match.", file=sys.stderr)
        sys.exit(1)

    if len(password) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        sys.exit(1)

    asyncio.run(create_superuser(email, name, password))


if __name__ == "__main__":
    main()