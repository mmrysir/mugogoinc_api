from __future__ import annotations

from typing import Optional


def mask_phone(phone: Optional[str]) -> str:
    """Masks phone numbers except country code and last 3 digits: e.g. +254 XXX XXX 248"""
    if not phone or len(phone.strip()) < 7:
        return "+XXX XXX XXX"
    clean = phone.strip()
    return f"{clean[:4]} XXX XXX {clean[-3:]}"


def mask_email(email: Optional[str]) -> str:
    """Masks email username: e.g. j***n@gmail.com"""
    if not email or "@" not in email:
        return "*****@domain.com"
    try:
        name, domain = email.strip().split("@", 1)
        if not name or not domain:
            return "*****@domain.com"
        if len(name) > 2:
            masked_name = f"{name[0]}***{name[-1]}"
        elif len(name) == 2:
            masked_name = f"{name[0]}***"
        else:
            masked_name = "***"
        return f"{masked_name}@{domain}"
    except Exception:
        return "*****@domain.com"


def mask_name(name: Optional[str]) -> str:
    """Masks full names: e.g. J*** D***"""
    if not name or not name.strip() or name.strip() == "Anonymous Lead":
        return "Anonymous Lead"
    parts = name.strip().split()
    masked_parts = []
    for part in parts:
        if len(part) > 1:
            masked_parts.append(f"{part[0]}***")
        else:
            masked_parts.append("*")
    return " ".join(masked_parts)
