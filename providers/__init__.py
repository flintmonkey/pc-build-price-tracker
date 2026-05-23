"""Price provider registry."""

from __future__ import annotations

import logging

from providers import (
    api_amazon,
    api_bestbuy,
    scrape_bestbuy,
    scrape_bhphoto,
    scrape_microcenter,
    scrape_newegg,
)
from providers.base import price_source_for

log = logging.getLogger(__name__)

_SCRAPE_BY_STORE = {
    "newegg": scrape_newegg,
    "bhphoto": scrape_bhphoto,
    "microcenter": scrape_microcenter,
}

_API_BY_STORE = {
    "amazon": api_amazon,
    "bestbuy": api_bestbuy,
}


def fetch_price(product: dict, config: dict) -> tuple[float | None, bool | None, float | None]:
    store = product.get("store", "").lower()
    source = price_source_for(product, store, config)

    if source == "api":
        api_mod = _API_BY_STORE.get(store)
        if api_mod:
            price, in_stock, msrp = api_mod.fetch(product, config)
            if price is not None:
                return price, in_stock, msrp
            if store == "bestbuy":
                log.info("Best Buy API unavailable — falling back to scrape for %s", product.get("name"))
                return scrape_bestbuy.fetch(product, config)
            return None, None, None
        log.error("No API provider for store '%s'", store)
        return None, None, None

    if store == "bestbuy":
        return scrape_bestbuy.fetch(product, config)

    scrape_mod = _SCRAPE_BY_STORE.get(store)
    if scrape_mod:
        return scrape_mod.fetch(product, config)

    log.error("Unknown store '%s' for product '%s'", store, product.get("name"))
    return None, None, None
