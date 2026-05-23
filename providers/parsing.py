"""Shared HTML price and stock parsing helpers."""

import json
import re

from bs4 import BeautifulSoup

OOS_PHRASES = frozenset([
    "out of stock",
    "sold out",
    "currently unavailable",
    "temporarily out of stock",
    "notify me when available",
    "notify when available",
    "item is no longer available",
])


def parse_price(text: str) -> float | None:
    text = text.strip().replace(",", "")
    match = re.search(r"\$?([\d]+\.[\d]{2})", text)
    if match:
        return float(match.group(1))
    match = re.search(r"\$?([\d]+)", text)
    if match:
        return float(match.group(1))
    return None


def detect_stock(soup: BeautifulSoup, price: float | None) -> bool | None:
    page_text = soup.get_text(" ", strip=True).lower()
    if any(phrase in page_text for phrase in OOS_PHRASES):
        return False
    if price is not None:
        return True
    return None


def price_from_json_ld(soup: BeautifulSoup) -> float | None:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            offers = data.get("offers", {})
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            raw = offers.get("price") or offers.get("lowPrice")
            if raw:
                return float(str(raw).replace(",", ""))
        except (json.JSONDecodeError, ValueError, AttributeError, TypeError):
            continue
    return None


def msrp_from_text(soup: BeautifulSoup) -> float | None:
    text = soup.get_text()
    match = re.search(r"MSRP[:\s]*\$?([\d,]+\.[\d]{2})", text, re.IGNORECASE)
    if match:
        return float(match.group(1).replace(",", ""))
    return None


def filter_anomalous_lows(prices: list[float]) -> list[float]:
    if len(prices) < 3:
        return prices
    sorted_prices = sorted(prices)
    n = len(sorted_prices)
    q1 = sorted_prices[n // 4]
    q3 = sorted_prices[3 * n // 4]
    lower_bound = q1 - 1.5 * (q3 - q1)
    return [p for p in prices if p >= lower_bound]
