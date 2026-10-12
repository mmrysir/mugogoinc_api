import pytest
from unittest.mock import AsyncMock, patch
from app.services.places import fetch_google_place_details


@pytest.mark.asyncio
async def test_fetch_google_place_details_empty_id():
    res = await fetch_google_place_details("")
    assert res is None


@pytest.mark.asyncio
async def test_fetch_google_place_details_no_api_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    res = await fetch_google_place_details("ChIJN1t_tDeuEmsRUsoyG83frY4")
    assert res is None


@pytest.mark.asyncio
async def test_fetch_google_place_details_mock_success(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "mock-test-key")

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "displayName": {"text": "Mugogo Safaris & Beach Tours"},
        "rating": 4.9,
        "userRatingCount": 98,
        "businessStatus": "OPERATIONAL",
        "googleMapsUri": "https://maps.google.com/?cid=123456789",
    }

    with patch("httpx.AsyncClient.get", return_value=mock_resp):
        res = await fetch_google_place_details("ChIJN1t_tDeuEmsRUsoyG83frY4")
        assert res is not None
        assert res["rating"] == 4.9
        assert res["review_count"] == 98
        assert res["business_status"] == "OPERATIONAL"
        assert res["map_url"] == "https://maps.google.com/?cid=123456789"
