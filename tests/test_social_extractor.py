import pytest
from app.utils.social_extractor import extract_social_links, clean_social_url


def test_clean_social_url():
    assert clean_social_url("http://www.linkedin.com/company/mugogoinc/") == "https://www.linkedin.com/company/mugogoinc"
    assert clean_social_url("instagram.com/mugogoinc/") == "https://instagram.com/mugogoinc"


def test_extract_social_links():
    urls = [
        "https://www.linkedin.com/company/mugogo-inc",
        "https://facebook.com/mugogoofficial",
        "https://www.instagram.com/mugogo.ke",
        "https://x.com/mugogo_inc",
        "https://www.youtube.com/@mugogotv",
        "https://tiktok.com/@mugogosafaris",
        "https://maps.google.com/?cid=1234567890123456",
        # Bad / sharing URLs should be ignored
        "https://www.facebook.com/sharer/sharer.php?u=example.com",
        "https://twitter.com/intent/tweet?text=hello",
        "https://example.com/blog/article-1",
    ]

    socials = extract_social_links(urls)

    assert "linkedin" in socials
    assert socials["linkedin"] == "https://www.linkedin.com/company/mugogo-inc"

    assert "facebook" in socials
    assert socials["facebook"] == "https://facebook.com/mugogoofficial"

    assert "instagram" in socials
    assert socials["instagram"] == "https://www.instagram.com/mugogo.ke"

    assert "twitter" in socials
    assert socials["twitter"] == "https://x.com/mugogo_inc"

    assert "youtube" in socials
    assert socials["youtube"] == "https://www.youtube.com/@mugogotv"

    assert "tiktok" in socials
    assert socials["tiktok"] == "https://tiktok.com/@mugogosafaris"

    assert "google_business" in socials
    assert socials["google_business"] == "https://maps.google.com/?cid=1234567890123456"

    assert len(socials) == 7
