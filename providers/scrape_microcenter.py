"""Micro Center HTML price provider."""

import logging
import re

from bs4 import BeautifulSoup

from providers.base import microcenter_store_id
from providers.http import get_scraper
from providers.parsing import detect_stock, msrp_from_text, parse_price, price_from_json_ld

log = logging.getLogger(__name__)


def fetch(product: dict, config: dict) -> tuple[float | None, bool | None, float | None]:
    url = product["url"]
    store_id = microcenter_store_id(config)
    cookies = {"storeSelected": store_id}
    try:
        resp = get_scraper("microcenter").get(url, cookies=cookies, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        log.warning("Microcenter fetch failed for %s: %s", url, e)
        return None, None, None

    soup = BeautifulSoup(resp.text, "html.parser")
    price = None

    price_el = soup.find(itemprop="price")
    if price_el:
        raw = price_el.get("content") or price_el.get_text(strip=True)
        price = parse_price(str(raw))

    if price is None:
        pricing = soup.select_one("#pricing .price, #pricing [class*='price']")
        if pricing:
            price = parse_price(pricing.get_text(strip=True))

    if price is None:
        price = price_from_json_ld(soup)

    msrp = msrp_from_text(soup)

    if price is None:
        log.warning("Could not parse Microcenter price from %s", url)

    in_stock = None
    avail_el = soup.select_one("#inStoreAvailability, [id*='availability']")
    if avail_el:
        avail_text = avail_el.get_text(strip=True).lower()
        if "in stock" in avail_text or re.search(r"\d+\s+in stock", avail_text):
            in_stock = True
        elif "out of stock" in avail_text or "not available" in avail_text:
            in_stock = False

    if in_stock is None:
        in_stock = detect_stock(soup, price)

    return price, in_stock, msrp
