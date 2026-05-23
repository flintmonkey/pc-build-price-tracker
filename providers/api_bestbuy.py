"""Best Buy Developer API (BBYOpen) price provider."""

from __future__ import annotations

import logging
import re

import requests

from providers.base import bestbuy_api_key, extract_bestbuy_sku

log = logging.getLogger(__name__)

API_BASE = "https://api.bestbuy.com/v1"


def _search_sku_by_name(name: str, api_key: str) -> str | None:
    """Find numeric SKU via Best Buy search when URL has no SKU."""
    # Use model/SKU fragment from product name when present
    search_term = name
    sku_match = re.search(r"\(([A-Z0-9-]+)\)\s*$", name)
    if sku_match:
        search_term = sku_match.group(1)
    else:
        search_term = name[:60]

    try:
        resp = requests.get(
            f"{API_BASE}/products",
            params={
                "apiKey": api_key,
                "format": "json",
                "pageSize": 1,
                "show": "sku",
                "search": search_term,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        products = data.get("products") or []
        if products:
            return str(products[0].get("sku"))
    except requests.RequestException as e:
        log.warning("Best Buy search failed for '%s': %s", search_term, e)
    return None


def fetch(product: dict, config: dict) -> tuple[float | None, bool | None, float | None]:
    api_key = bestbuy_api_key()
    if not api_key:
        log.warning("Best Buy API: BESTBUY_API_KEY not set — skipping %s", product.get("name"))
        return None, None, None

    sku = extract_bestbuy_sku(product)
    if not sku:
        sku = _search_sku_by_name(product.get("name", ""), api_key)
    if not sku:
        log.warning("Best Buy API: could not determine SKU for %s", product.get("name"))
        return None, None, None

    try:
        resp = requests.get(
            f"{API_BASE}/products/{sku}.json",
            params={
                "apiKey": api_key,
                "format": "json",
                "show": "salePrice,regularPrice,onlineAvailability,active",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        log.warning("Best Buy API fetch failed for SKU %s: %s", sku, e)
        return None, None, None

    price = None
    sale = data.get("salePrice")
    regular = data.get("regularPrice")
    if sale is not None:
        price = float(sale)
    elif regular is not None:
        price = float(regular)

    msrp = float(regular) if regular is not None else None

    in_stock = None
    avail = data.get("onlineAvailability")
    if avail is True:
        in_stock = True
    elif avail is False:
        in_stock = False

    if price is None:
        log.warning("Best Buy API: no price for SKU %s", sku)

    return price, in_stock, msrp
