"""Newegg HTML price provider."""

import logging
import re

import requests
from bs4 import BeautifulSoup

from providers.http import HEADERS
from providers.parsing import detect_stock, msrp_from_text, parse_price

log = logging.getLogger(__name__)


def fetch(product: dict, config: dict) -> tuple[float | None, bool | None, float | None]:
    url = product["url"]
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("Newegg fetch failed for %s: %s", url, e)
        return None, None, None

    soup = BeautifulSoup(resp.text, "html.parser")
    price = None

    price_el = soup.select_one("div.price-new-right .price-current")
    if price_el:
        strong = price_el.find("strong")
        sup = price_el.find("sup")
        if strong and sup:
            try:
                dollars = strong.get_text(strip=True).replace(",", "")
                cents = sup.get_text(strip=True).lstrip(".")
                price = float(f"{dollars}.{cents}")
            except ValueError:
                pass
        if price is None:
            price = parse_price(price_el.get_text(strip=True))

    if price is None:
        for el in soup.select(".price-current"):
            candidate = parse_price(el.get_text(strip=True))
            if candidate is not None:
                price = candidate
                break

    if price is None:
        og_price = soup.find("meta", property="og:price:amount")
        if og_price and og_price.get("content"):
            price = parse_price(og_price["content"])

    msrp = None
    msrp_el = soup.find("span", class_="price-msrp")
    if msrp_el:
        msrp = parse_price(msrp_el.get_text(strip=True))
    if msrp is None:
        msrp = msrp_from_text(soup)

    if price is None:
        log.warning("Could not parse Newegg price from %s", url)

    return price, detect_stock(soup, price), msrp
