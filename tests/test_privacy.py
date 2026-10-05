import pytest
from app.utils.privacy import mask_phone, mask_email, mask_name


def test_mask_phone():
    assert mask_phone("+254712345248") == "+254 XXX XXX 248"
    assert mask_phone("+1234567") == "+123 XXX XXX 567"
    assert mask_phone(None) == "+XXX XXX XXX"
    assert mask_phone("123") == "+XXX XXX XXX"


def test_mask_email():
    assert mask_email("john.doe@gmail.com") == "j***e@gmail.com"
    assert mask_email("ab@domain.com") == "a***@domain.com"
    assert mask_email("a@domain.com") == "***@domain.com"
    assert mask_email(None) == "*****@domain.com"
    assert mask_email("invalid-email") == "*****@domain.com"


def test_mask_name():
    assert mask_name("John Doe") == "J*** D***"
    assert mask_name("Jane Mary Watson") == "J*** M*** W***"
    assert mask_name("A") == "*"
    assert mask_name(None) == "Anonymous Lead"
    assert mask_name("Anonymous Lead") == "Anonymous Lead"
