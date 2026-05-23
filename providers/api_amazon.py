"""Amazon Product Advertising API 5.0 price provider."""

from __future__ import annotations

import logging

from providers.base import amazon_paapi_credentials, extract_asin

log = logging.getLogger(__name__)


def fetch(product: dict, config: dict) -> tuple[float | None, bool | None, float | None]:
    asin = extract_asin(product)
    if not asin:
        log.warning("Amazon API: no ASIN for %s", product.get("name"))
        return None, None, None

    creds = amazon_paapi_credentials()
    if not creds:
        log.warning(
            "Amazon API: missing AMAZON_PAAPI_ACCESS_KEY, AMAZON_PAAPI_SECRET_KEY, "
            "or AMAZON_ASSOCIATE_TAG — skipping %s",
            product.get("name"),
        )
        return None, None, None

    access_key, secret_key, partner_tag = creds

    try:
        from paapi5_python_sdk.api.default_api import DefaultApi
        from paapi5_python_sdk.models.get_items_request import GetItemsRequest
        from paapi5_python_sdk.models.get_items_resource import GetItemsResource
        from paapi5_python_sdk.models.partner_type import PartnerType
        from paapi5_python_sdk.rest import ApiException
    except ImportError:
        log.error("paapi5-python-sdk not installed — cannot fetch Amazon prices via API")
        return None, None, None

    try:
        api = DefaultApi(
            access_key=access_key,
            secret_key=secret_key,
            host="webservices.amazon.com",
            region="us-east-1",
        )
        request = GetItemsRequest(
            partner_tag=partner_tag,
            partner_type=PartnerType.ASSOCIATES,
            marketplace="www.amazon.com",
            item_ids=[asin],
            resources=[
                GetItemsResource.OFFERS_LISTINGS_PRICE,
                GetItemsResource.OFFERS_LISTINGS_AVAILABILITY_MESSAGE,
                GetItemsResource.OFFERS_LISTINGS_CONDITION,
            ],
        )
        response = api.get_items(request)
    except ApiException as e:
        log.warning("Amazon PA-API error for ASIN %s: %s", asin, e)
        return None, None, None
    except Exception as e:
        log.warning("Amazon PA-API failed for ASIN %s: %s", asin, e)
        return None, None, None

    if not response.items_result or not response.items_result.items:
        log.warning("Amazon PA-API: no item returned for ASIN %s", asin)
        return None, None, None

    item = response.items_result.items[0]
    price = None
    in_stock = None
    msrp = None

    if item.offers and item.offers.listings:
        listing = item.offers.listings[0]
        if listing.price and listing.price.amount is not None:
            price = float(listing.price.amount)
        if listing.availability and listing.availability.message:
            msg = listing.availability.message.lower()
            if "in stock" in msg or "available" in msg:
                in_stock = True
            elif "unavailable" in msg or "out of stock" in msg:
                in_stock = False

    if price is None:
        log.warning("Amazon PA-API: no price in response for ASIN %s", asin)

    return price, in_stock, msrp
