"""B&H Photo HTML price provider."""

import logging

from bs4 import BeautifulSoup

from providers.http import get_scraper
from providers.parsing import detect_stock, msrp_from_text, parse_price, price_from_json_ld

log = logging.getLogger(__name__)


def fetch(product: dict, config: dict) -> tuple[float | None, bool | None, float | None]:
    url = product["url"]
    try:
        resp = get_scraper("bhphoto").get(url, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        log.warning("B&H fetch failed for %s: %s", url, e)
        return None, None, None

    soup = BeautifulSoup(resp.text, "html.parser")
    price = None

    price_el = soup.find(attrs={"data-selenium": "pricingPrice"})
    if price_el:
        price = parse_price(price_el.get_text(strip=True))

    if price is None:
        price = price_from_json_ld(soup)

    if price is None:
        og_price = soup.find("meta", property="og:price:amount")
        if og_price and og_price.get("content"):
            price = parse_price(og_price["content"])

    msrp = msrp_from_text(soup)

    if price is None:
        log.warning("Could not parse B&H price from %s", url)

    return price, detect_stock(soup, price), msrp
