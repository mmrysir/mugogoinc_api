import logging
from datetime import datetime
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from app.core.config import settings

logger = logging.getLogger("mugogo_api")

def upload_offline_conversion(gclid: str, conversion_time: datetime = None, conversion_value: float = 1.0):
    if not gclid:
        logger.warning("Google Ads CAPI: Skipped upload due to missing GCLID")
        return False

    if not settings.google_ads_developer_token:
        logger.info(f"Google Ads CAPI [STUB]: Captured GCLID {gclid} (Credentials not configured)")
        return True

    credentials = {
        "developer_token": settings.google_ads_developer_token,
        "client_id": settings.google_ads_client_id,
        "client_secret": settings.google_ads_client_secret,
        "refresh_token": settings.google_ads_refresh_token,
        "use_proto_plus": True,
    }

    try:
        client = GoogleAdsClient.load_from_dict(credentials)
        conversion_upload_service = client.get_service("ConversionUploadService")
        
        customer_id = settings.google_ads_customer_id.replace("-", "")
        conversion_action = f"customers/{customer_id}/conversionActions/{settings.google_ads_conversion_action_id}"

        click_conversion = client.get_type("ClickConversion")
        click_conversion.conversion_action = conversion_action
        click_conversion.gclid = gclid
        
        # Format time to "yyyy-mm-dd hh:mm:ss+hh:mm"
        formatted_time = (conversion_time or datetime.now()).strftime("%Y-%m-%d %H:%M:%S+03:00")
        click_conversion.conversion_date_time = formatted_time
        click_conversion.conversion_value = conversion_value
        click_conversion.currency_code = "USD"

        request = client.get_type("UploadClickConversionsRequest")
        request.customer_id = customer_id
        request.conversions = [click_conversion]
        request.partial_failure = True

        response = conversion_upload_service.upload_click_conversions(request=request)

        if response.partial_failure_error:
            logger.error(f"Google Ads CAPI Upload Partial Error: {response.partial_failure_error.message}")
            return False

        logger.info(f"Google Ads CAPI Upload Success for GCLID: {gclid}")
        return True

    except GoogleAdsException as ex:
        logger.error(f"Google Ads API Exception: {ex}")
        return False
    except Exception as e:
        logger.error(f"Google Ads Upload Error: {str(e)}")
        return False
