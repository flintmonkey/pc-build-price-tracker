"""Best Buy HTML price provider (fallback when API key is not set)."""

import logging

from bs4 import BeautifulSoup

from providers.http import get_scraper
from providers.parsing import detect_stock, msrp_from_text, parse_price, price_from_json_ld

log = logging.getLogger(__name__)


def fetch(product: dict, config: dict) -> tuple[float | None, bool | None, float | None]:
    url = product["url"]
    try:
        resp = get_scraper("bestbuy").get(url, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        log.warning("Best Buy scrape failed for %s: %s", url, e)
        return None, None, None

    soup = BeautifulSoup(resp.text, "html.parser")
    price = None

    for selector in (
        ".priceView-customer-price span[aria-hidden='true']",
        ".priceView-hero-price span[aria-hidden='true']",
        ".priceView-customer-price span",
        "[data-testid='customer-price'] span[aria-hidden='true']",
    ):
        el = soup.select_one(selector)
        if el:
            price = parse_price(el.get_text(strip=True))
            if price:
                break

    if price is None:
        price = price_from_json_ld(soup)

    if price is None:
        og_price = soup.find("meta", property="og:price:amount")
        if og_price and og_price.get("content"):
            price = parse_price(og_price["content"])

    msrp = msrp_from_text(soup)

    if price is None:
        log.warning("Could not parse Best Buy price from %s", url)

    in_stock = None
    page_text = soup.get_text(" ", strip=True).lower()
    if "sold out" in page_text or "coming soon" in page_text:
        in_stock = False
    elif soup.select_one("[data-button-state='ADD_TO_CART'], .add-to-cart-button"):
        in_stock = True
    else:
        in_stock = detect_stock(soup, price)

    return price, in_stock, msrp
