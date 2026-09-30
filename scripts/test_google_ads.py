import sys
import os
import asyncio

# Ensure app is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.google_ads import upload_offline_conversion
from app.core.config import settings

def test_google_ads_stub():
    print("--- Running Google Ads CAPI Dry-Run ---")
    
    if not settings.google_ads_developer_token:
        print("[INFO] Developer token not set. Expecting STUB behavior.")
    
    # Test with a dummy gclid
    dummy_gclid = "Cj0KCQ_test_google_click_id"
    
    # Call the service
    result = upload_offline_conversion(dummy_gclid)
    
    print(f"Result: {'SUCCESS' if result else 'FAILED'}")
    
if __name__ == "__main__":
    test_google_ads_stub()
