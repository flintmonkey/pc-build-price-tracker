#!/usr/bin/env python3
"""Daily price tracker for PC parts across Newegg, B&H Photo, Amazon, NVIDIA, and Microcenter."""

import json
import logging
import os
import re
import smtplib
import sys
from collections import defaultdict
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import html
import urllib.request

import certifi
import cloudscraper
import feedparser
import requests
import yaml
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.yaml"
HISTORY_FILE = BASE_DIR / "prices_history.json"
CAMEL_SEEN_FILE = BASE_DIR / "camel_seen.json"
LOG_FILE = BASE_DIR / "price_tracker.log"
DASHBOARD_FILE = BASE_DIR / "dashboard.html"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Per-domain cloudscraper instances so a 403 on one site doesn't poison another.
_scrapers: dict[str, cloudscraper.CloudScraper] = {}


def _get_scraper(key: str) -> cloudscraper.CloudScraper:
    if key not in _scrapers:
        _scrapers[key] = cloudscraper.create_scraper()
    return _scrapers[key]


OOS_PHRASES = frozenset([
    "out of stock",
    "sold out",
    "currently unavailable",
    "temporarily out of stock",
    "notify me when available",
    "notify when available",
    "item is no longer available",
])


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        log.error("config.yaml not found at %s", CONFIG_FILE)
        sys.exit(1)
    with open(CONFIG_FILE) as f:
        cfg = yaml.safe_load(f)
    # Allow GitHub Actions secrets to override config credentials
    for env_var, key in (
        ("GMAIL_SENDER", "sender"),
        ("GMAIL_PASSWORD", "password"),
        ("GMAIL_RECIPIENT", "recipient"),
    ):
        val = os.environ.get(env_var)
        if val:
            cfg["email"][key] = val
    return cfg


def load_history() -> dict:
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE) as f:
            return json.load(f)
    return {}


def save_history(history: dict) -> None:
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def _parse_price(text: str) -> float | None:
    text = text.strip().replace(",", "")
    match = re.search(r"\$?([\d]+\.[\d]{2})", text)
    if match:
        return float(match.group(1))
    match = re.search(r"\$?([\d]+)", text)
    if match:
        return float(match.group(1))
    return None


def _detect_stock(soup: BeautifulSoup, price: float | None) -> bool | None:
    """Return True=in stock, False=out of stock, None=unknown."""
    page_text = soup.get_text(" ", strip=True).lower()
    if any(phrase in page_text for phrase in OOS_PHRASES):
        return False
    if price is not None:
        return True
    return None


def fetch_newegg_price(url: str) -> tuple[float | None, bool | None, float | None]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("Newegg fetch failed for %s: %s", url, e)
        return None, None, None

    soup = BeautifulSoup(resp.text, "html.parser")
    price = None

    # Primary: prefer the main product price shown in the price-new-right block.
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
            price = _parse_price(price_el.get_text(strip=True))

    # Fallback: try any valid price-current block if the preferred selector is missing.
    if price is None:
        for el in soup.select(".price-current"):
            text = el.get_text(strip=True)
            if not text:
                continue
            candidate = _parse_price(text)
            if candidate is not None:
                price = candidate
                break

    # Fallback: og:price meta tag
    if price is None:
        og_price = soup.find("meta", property="og:price:amount")
        if og_price and og_price.get("content"):
            price = _parse_price(og_price["content"])

    # MSRP fetching
    msrp = None
    # Try specific class if exists
    msrp_el = soup.find('span', class_='price-msrp')
    if msrp_el:
        msrp = _parse_price(msrp_el.get_text(strip=True))
    # Fallback: search for MSRP in text
    if msrp is None:
        text = soup.get_text()
        match = re.search(r'MSRP[:\s]*\$?([\d,]+\.[\d]{2})', text, re.IGNORECASE)
        if match:
            msrp = float(match.group(1).replace(',', ''))

    if price is None:
        log.warning("Could not parse Newegg price from %s", url)

    return price, _detect_stock(soup, price), msrp


def fetch_bhphoto_price(url: str) -> tuple[float | None, bool | None, float | None]:
    try:
        resp = _get_scraper("bhphoto").get(url, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        log.warning("B&H fetch failed for %s: %s", url, e)
        return None, None, None

    soup = BeautifulSoup(resp.text, "html.parser")
    price = None

    # Primary: data-selenium="pricingPrice"
    price_el = soup.find(attrs={"data-selenium": "pricingPrice"})
    if price_el:
        price = _parse_price(price_el.get_text(strip=True))

    # Fallback 1: JSON-LD structured data
    if price is None:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                offers = data.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0]
                raw = offers.get("price") or offers.get("lowPrice")
                if raw:
                    price = float(str(raw).replace(",", ""))
                    break
            except (json.JSONDecodeError, ValueError, AttributeError):
                continue

    # Fallback 2: og:price meta tag
    if price is None:
        og_price = soup.find("meta", property="og:price:amount")
        if og_price and og_price.get("content"):
            price = _parse_price(og_price["content"])

    # MSRP fetching
    msrp = None
    text = soup.get_text()
    match = re.search(r'MSRP[:\s]*\$?([\d,]+\.[\d]{2})', text, re.IGNORECASE)
    if match:
        msrp = float(match.group(1).replace(',', ''))

    if price is None:
        log.warning("Could not parse B&H price from %s", url)

    return price, _detect_stock(soup, price), msrp


def fetch_amazon_price(url: str) -> tuple[float | None, bool | None, float | None]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("Amazon fetch failed for %s: %s", url, e)
        return None, None, None

    soup = BeautifulSoup(resp.text, "html.parser")
    price = None

    # Primary: .a-price .a-offscreen — screen-reader text with the full price string
    price_el = soup.select_one(".a-price .a-offscreen")
    if price_el:
        price = _parse_price(price_el.get_text(strip=True))

    # Fallback 1: apex price block used in newer listing pages
    if price is None:
        apex = soup.select_one(".apexPriceToPay .a-offscreen")
        if apex:
            price = _parse_price(apex.get_text(strip=True))

    # Fallback 2: legacy price block IDs
    if price is None:
        for selector in ("#priceblock_dealprice", "#priceblock_ourprice", "#price_inside_buybox"):
            el = soup.select_one(selector)
            if el:
                price = _parse_price(el.get_text(strip=True))
                if price:
                    break

    # Fallback 3: JSON-LD structured data
    if price is None:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                offers = data.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0]
                raw = offers.get("price") or offers.get("lowPrice")
                if raw:
                    price = float(str(raw).replace(",", ""))
                    break
            except (json.JSONDecodeError, ValueError, AttributeError):
                continue

    # MSRP fetching
    msrp = None
    text = soup.get_text()
    match = re.search(r'MSRP[:\s]*\$?([\d,]+\.[\d]{2})', text, re.IGNORECASE)
    if match:
        msrp = float(match.group(1).replace(',', ''))

    if price is None:
        log.warning("Could not parse Amazon price from %s", url)

    # Amazon-specific stock detection via the #availability element
    in_stock = None
    avail_el = soup.select_one("#availability")
    if avail_el:
        avail_text = avail_el.get_text(strip=True).lower()
        if "in stock" in avail_text or "only" in avail_text:
            in_stock = True
        elif any(p in avail_text for p in ("out of stock", "unavailable", "cannot be shipped")):
            in_stock = False

    if in_stock is None:
        in_stock = _detect_stock(soup, price)

    return price, in_stock, msrp


def fetch_nvidia_price(url: str) -> tuple[float | None, bool | None, float | None]:
    # NVIDIA Marketplace blocks programmatic access; short timeout so it fails fast.
    # The URL is kept in config for the dashboard hyperlink.
    try:
        resp = requests.get(url, headers=HEADERS, timeout=5)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("NVIDIA fetch failed (site blocks scrapers) for %s: %s", url, e)
        return None, None, None

    soup = BeautifulSoup(resp.text, "html.parser")
    price = None

    # Primary: JSON-LD structured data
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            offers = data.get("offers", {})
            if isinstance(offers, list):
                offers = offers[0]
            raw = offers.get("price") or offers.get("lowPrice")
            if raw:
                price = float(str(raw).replace(",", ""))
                break
        except (json.JSONDecodeError, ValueError, AttributeError):
            continue

    # Fallback 1: og:price meta tag
    if price is None:
        og_price = soup.find("meta", property="og:price:amount")
        if og_price and og_price.get("content"):
            price = _parse_price(og_price["content"])

    # Fallback 2: common price element selectors
    if price is None:
        for selector in ("[data-price]", ".product-price", ".price", "#price"):
            el = soup.select_one(selector)
            if el:
                raw = el.get("data-price") or el.get_text(strip=True)
                price = _parse_price(str(raw))
                if price:
                    break

    # MSRP fetching - unlikely to work due to blocking
    msrp = None

    if price is None:
        log.warning("Could not parse NVIDIA price from %s", url)

    return price, _detect_stock(soup, price), msrp


def fetch_microcenter_price(url: str) -> tuple[float | None, bool | None, float | None]:
    # storeSelected=121 pins pricing and stock to the Cambridge/Boston location
    cookies = {"storeSelected": "121"}
    try:
        resp = _get_scraper("microcenter").get(url, cookies=cookies, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        log.warning("Microcenter fetch failed for %s: %s", url, e)
        return None, None, None

    soup = BeautifulSoup(resp.text, "html.parser")
    price = None

    # Primary: itemprop="price" microdata (content attr holds the numeric value)
    price_el = soup.find(itemprop="price")
    if price_el:
        raw = price_el.get("content") or price_el.get_text(strip=True)
        price = _parse_price(str(raw))

    # Fallback 1: #pricing section
    if price is None:
        pricing = soup.select_one("#pricing .price, #pricing [class*='price']")
        if pricing:
            price = _parse_price(pricing.get_text(strip=True))

    # Fallback 2: JSON-LD structured data
    if price is None:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                offers = data.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0]
                raw = offers.get("price") or offers.get("lowPrice")
                if raw:
                    price = float(str(raw).replace(",", ""))
                    break
            except (json.JSONDecodeError, ValueError, AttributeError):
                continue

    # MSRP fetching
    msrp = None
    text = soup.get_text()
    match = re.search(r'MSRP[:\s]*\$?([\d,]+\.[\d]{2})', text, re.IGNORECASE)
    if match:
        msrp = float(match.group(1).replace(',', ''))

    if price is None:
        log.warning("Could not parse Microcenter price from %s", url)

    # Microcenter-specific stock detection
    in_stock = None
    avail_el = soup.select_one("#inStoreAvailability, [id*='availability']")
    if avail_el:
        avail_text = avail_el.get_text(strip=True).lower()
        if "in stock" in avail_text or re.search(r"\d+\s+in stock", avail_text):
            in_stock = True
        elif "out of stock" in avail_text or "not available" in avail_text:
            in_stock = False

    if in_stock is None:
        in_stock = _detect_stock(soup, price)

    return price, in_stock, msrp


def load_camel_seen() -> set[str]:
    if CAMEL_SEEN_FILE.exists():
        return set(json.loads(CAMEL_SEEN_FILE.read_text()))
    return set()


def save_camel_seen(seen: set[str]) -> None:
    # Cap at 500 entries so the file doesn't grow unbounded
    CAMEL_SEEN_FILE.write_text(json.dumps(list(seen)[-500:]))


def fetch_camelcamelcamel_alerts(config: dict, seen_ids: set[str]) -> list[dict]:
    """Parse the CamelCamelCamel RSS feed and return unseen price-drop entries."""
    rss_url = config.get("camelcamelcamel", {}).get("rss_url", "").strip()
    if not rss_url:
        return []

    log.info("Checking CamelCamelCamel RSS feed...")
    # macOS Python doesn't trust system certs by default; use certifi's bundle
    import ssl
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    https_handler = urllib.request.HTTPSHandler(context=ssl_ctx)
    feed = feedparser.parse(rss_url, handlers=[https_handler])

    if feed.bozo and not feed.entries:
        log.warning("CamelCamelCamel feed could not be parsed: %s", feed.bozo_exception)
        return []

    alerts = []
    for entry in feed.entries:
        entry_id = entry.get("id") or entry.get("link", "")
        if not entry_id or entry_id in seen_ids:
            continue

        title = entry.get("title", "Unknown product")
        link = entry.get("link", "")
        summary = entry.get("summary", "")

        # Try to pull the price out of the title first, then the summary
        price = _parse_price(title) or _parse_price(summary)

        log.info("  CamelCamelCamel alert: %s", title)
        alerts.append({
            "name": title,
            "url": link,
            "store": "camelcamelcamel",
            "current_price": price,
            "target_price": None,
            "summary": summary,
            "entry_id": entry_id,
        })

    return alerts


def fetch_bestbuy_price(url: str) -> tuple[float | None, bool | None, float | None]:
    try:
        resp = _get_scraper("bestbuy").get(url, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        log.warning("Best Buy fetch failed for %s: %s", url, e)
        return None, None, None

    soup = BeautifulSoup(resp.text, "html.parser")
    price = None

    # Primary: .priceView-customer-price or .priceView-hero-price
    for selector in (
        ".priceView-customer-price span[aria-hidden='true']",
        ".priceView-hero-price span[aria-hidden='true']",
        ".priceView-customer-price span",
        "[data-testid='customer-price'] span[aria-hidden='true']",
    ):
        el = soup.select_one(selector)
        if el:
            price = _parse_price(el.get_text(strip=True))
            if price:
                break

    # Fallback: JSON-LD structured data
    if price is None:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                offers = data.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0]
                raw = offers.get("price") or offers.get("lowPrice")
                if raw:
                    price = float(str(raw).replace(",", ""))
                    break
            except (json.JSONDecodeError, ValueError, AttributeError):
                continue

    # Fallback: og:price meta tag
    if price is None:
        og_price = soup.find("meta", property="og:price:amount")
        if og_price and og_price.get("content"):
            price = _parse_price(og_price["content"])

    # MSRP fetching
    msrp = None
    text = soup.get_text()
    match = re.search(r'MSRP[:\s]*\$?([\d,]+\.[\d]{2})', text, re.IGNORECASE)
    if match:
        msrp = float(match.group(1).replace(',', ''))

    if price is None:
        log.warning("Could not parse Best Buy price from %s", url)

    # Stock detection: look for add-to-cart button vs sold-out indicators
    in_stock = None
    page_text = soup.get_text(" ", strip=True).lower()
    if "sold out" in page_text or "coming soon" in page_text:
        in_stock = False
    elif soup.select_one("[data-button-state='ADD_TO_CART'], .add-to-cart-button"):
        in_stock = True
    else:
        in_stock = _detect_stock(soup, price)

    return price, in_stock, msrp


SCRAPERS = {
    "newegg": fetch_newegg_price,
    "bhphoto": fetch_bhphoto_price,
    "amazon": fetch_amazon_price,
    "nvidia": fetch_nvidia_price,
    "microcenter": fetch_microcenter_price,
    "bestbuy": fetch_bestbuy_price,
}


def fetch_price(product: dict) -> tuple[float | None, bool | None, float | None]:
    store = product.get("store", "").lower()
    scraper = SCRAPERS.get(store)
    if not scraper:
        log.error("Unknown store '%s' for product '%s'", store, product["name"])
        return None, None, None
    return scraper(product["url"])


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def _sparkline_svg(prices: list[float], width: int = 80, height: int = 24) -> str:
    if len(prices) < 2:
        return ""
    lo, hi = min(prices), max(prices)
    span = hi - lo or 1
    pts = []
    for i, p in enumerate(prices):
        x = round(i / (len(prices) - 1) * width, 1)
        y = round(height - ((p - lo) / span * (height - 4)) - 2, 1)
        pts.append(f"{x},{y}")
    return (
        f'<svg width="{width}" height="{height}" style="vertical-align:middle" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'<polyline points="{" ".join(pts)}" fill="none" stroke="#4a90d9" stroke-width="1.5"/>'
        f'</svg>'
    )


def _filter_anomalous_lows(prices: list[float]) -> list[float]:
    """Filter out prices that appear to be anomalous lows (e.g., parsing errors)."""
    if len(prices) < 3:
        return prices
    sorted_prices = sorted(prices)
    # Use IQR method: exclude prices below Q1 - 1.5*IQR
    n = len(sorted_prices)
    q1_idx = n // 4
    q3_idx = 3 * n // 4
    q1 = sorted_prices[q1_idx]
    q3 = sorted_prices[q3_idx]
    iqr = q3 - q1
    lower_bound = q1 - 1.5 * iqr
    return [p for p in prices if p >= lower_bound]


def _stock_badge(in_stock: bool | None) -> str:
    if in_stock is True:
        return (
            "<div style='margin-top:4px'><span style='background:#e8f5e9;color:#27ae60;"
            "border-radius:3px;padding:1px 5px;font-size:11px'>In Stock</span></div>"
        )
    if in_stock is False:
        return (
            "<div style='margin-top:4px'><span style='background:#fce4ec;color:#c0392b;"
            "border-radius:3px;padding:1px 5px;font-size:11px'>Out of Stock</span></div>"
        )
    return (
        "<span style='background:#f5f5f5;color:#aaa;border-radius:3px;"
        "padding:1px 5px;font-size:11px;margin-left:4px'>?</span>"
    )


def generate_dashboard(config: dict, history: dict) -> None:
    static_mode = bool(os.environ.get("GITHUB_ACTIONS"))
    products = config.get("products", [])
    today = datetime.now().strftime("%Y-%m-%d")

    # Preserve store column order as declared in config
    all_stores: list[str] = []
    seen_stores: set[str] = set()
    for p in products:
        s = p.get("store", "").lower()
        if s not in seen_stores:
            all_stores.append(s)
            seen_stores.add(s)

    # Group products by name, preserving declaration order
    groups: dict[str, list[dict]] = defaultdict(list)
    for p in products:
        groups[p["name"]].append(p)

    rows_html = ""
    row_idx = 0
    for product_name, entries in groups.items():
        target = float(entries[0]["target_price"])
        msrp = None
        for entry in entries:
            if entry.get("msrp") is not None:
                msrp = float(entry["msrp"])
                break
        if msrp is None:
            for entry in entries:
                records = history.get(entry["url"], [])
                latest = records[-1] if records else None
                if latest and latest.get("msrp") is not None:
                    msrp = latest["msrp"]
                    break

        # Per-store: latest price, stock, date, and last-30-day price series
        store_data: dict[str, dict] = {}
        all_prices_flat: list[float] = []
        for entry in entries:
            store = entry["store"].lower()
            records = history.get(entry["url"], [])
            latest = records[-1] if records else None
            prices_30d = [r["price"] for r in records[-30:] if r.get("price") is not None]
            store_data[store] = {
                "price": latest["price"] if latest else None,
                "in_stock": latest.get("in_stock") if latest else None,
                "date": latest["date"] if latest else None,
                "prices_30d": prices_30d,
                "url": entry["url"],
            }
            all_prices_flat.extend(prices_30d)

        # Filter out anomalous low prices (e.g., parsing errors) before calculating stats
        all_prices_flat = _filter_anomalous_lows(all_prices_flat)

        # Most recent fetch date across stores — used to highlight fresh cells
        dates = [d["date"] for d in store_data.values() if d["date"]]
        most_recent_date = max(dates) if dates else None

        low_30d = min(all_prices_flat) if all_prices_flat else None
        high_30d = max(all_prices_flat) if all_prices_flat else None

        # Sparkline merges all stores' history, sorted by date
        all_records: list[dict] = []
        for entry in entries:
            all_records.extend(history.get(entry["url"], []))
        all_records.sort(key=lambda r: r["date"])
        spark_prices = [r["price"] for r in all_records[-30:] if r.get("price") is not None]
        spark_prices = _filter_anomalous_lows(spark_prices)
        sparkline = _sparkline_svg(spark_prices) if len(spark_prices) >= 2 else "—"

        # Find the lowest current price across stores for "best price" badge
        live_prices = {
            s: d["price"] for s, d in store_data.items() if d["price"] is not None
        }
        lowest_price = min(live_prices.values()) if live_prices else None
        lowest_stores = {s for s, p in live_prices.items() if p == lowest_price} if lowest_price is not None else set()

        # Build one <td> per store column
        store_cols = ""
        for store in all_stores:
            if store not in store_data:
                store_cols += (
                    "<td style='color:#ccc;text-align:center;padding:8px;"
                    "border:1px solid #ddd'>—</td>"
                )
                continue

            cell = store_data[store]
            price = cell["price"]
            in_stock = cell["in_stock"]
            date = cell["date"]
            url = cell["url"]

            is_highlighted = date == most_recent_date == today
            cell_style = (
                "text-align:center;padding:8px;border:1px solid #ddd;"
                + ("background:#fffde7;outline:2px solid #f39c12;" if is_highlighted else "")
            )

            is_lowest = store in lowest_stores

            if price is None:
                price_html = f"<a href='{url}' target='_blank' style='color:#bbb;text-decoration:none'>N/A</a>"
            elif price < target:
                price_html = (
                    f"<a href='{url}' target='_blank' style='color:#27ae60;font-weight:bold;"
                    f"text-decoration:none'>${price:.2f}</a>"
                )
            else:
                price_html = (
                    f"<a href='{url}' target='_blank' style='color:#333;font-weight:bold;"
                    f"text-decoration:none'>${price:.2f}</a>"
                )

            best_badge = (
                "<div style='font-size:10px;font-weight:bold;color:#fff;"
                "background:#2ecc71;border-radius:3px;padding:1px 5px;"
                "margin-top:3px;display:inline-block'>★ Best Price</div>"
                if is_lowest else ""
            )

            percent_note = ""
            if price is not None and msrp is not None and msrp > 0:
                diff_pct = (price - msrp) / msrp * 100
                sign = "+" if diff_pct >= 0 else ""
                color = "#c0392b" if diff_pct > 0 else "#27ae60" if diff_pct < 0 else "#888"
                percent_note = (
                    f"<div style='font-size:11px;color:{color};margin-top:4px'>"
                    f"{sign}{diff_pct:.0f}% MSRP</div>"
                )

            stale_note = (
                f"<div style='color:#bbb;font-size:10px'>{date}</div>"
                if date and date != today
                else ""
            )

            store_cols += (
                f"<td style='{cell_style}'>"
                f"{price_html}{_stock_badge(in_stock)}{best_badge}{percent_note}{stale_note}"
                f"</td>"
            )

        low_text = f"${low_30d:.2f}" if low_30d is not None else "—"
        high_text = f"${high_30d:.2f}" if high_30d is not None else "—"

        safe_name = html.escape(product_name, quote=True)
        if static_mode:
            target_cell = (
                f"<td style='padding:8px;border:1px solid #ddd;text-align:center;"
                f"font-weight:bold'>${target:.2f}</td>"
            )
        else:
            target_cell = (
                f"<td style='padding:8px;border:1px solid #ddd;text-align:center'>"
                f"<div class='target-wrap' style='display:flex;align-items:center;"
                f"justify-content:center;gap:4px'>"
                f"<span style='color:#888;font-size:12px'>$</span>"
                f"<input type='number' class='target-input' step='0.01' min='0' "
                f"value='{target:.2f}' data-name='{safe_name}' "
                f"style='width:72px;border:1px solid #ccc;border-radius:3px;"
                f"padding:3px 5px;font-size:13px;text-align:right' "
                f"oninput='onTargetChange(this)'>"
                f"<button class='save-btn' onclick='saveTarget(this)' "
                f"style='display:none;padding:3px 10px;background:#4a90d9;color:#fff;"
                f"border:none;border-radius:3px;cursor:pointer;font-size:12px'>Save</button>"
                f"<span class='save-ok' "
                f"style='color:#27ae60;font-size:14px;display:none'>&#10003;</span>"
                f"</div></td>"
            )
        mfr_url = entries[0].get("manufacturer_url", "")
        name_html = (
            f"<a href='{html.escape(mfr_url)}' target='_blank' "
            f"style='color:#333;text-decoration:none'>{product_name}</a>"
            if mfr_url else product_name
        )
        msrp_text = f"${msrp:.2f}" if msrp is not None else "—"
        row_bg = " style='background-color:#f7f7f7'" if row_idx % 2 == 1 else ""
        rows_html += (
            f"<tr{row_bg}>"
            f"<td style='padding:8px;border:1px solid #ddd'>{name_html}</td>"
            f"{target_cell}"
            f"<td style='padding:8px;border:1px solid #ddd;text-align:center'>{msrp_text}</td>"
            f"{store_cols}"
            f"<td style='padding:8px;border:1px solid #ddd;text-align:center;"
            f"color:#27ae60;font-weight:bold'>{low_text}</td>"
            f"<td style='padding:8px;border:1px solid #ddd;text-align:center;"
            f"color:#c0392b;font-weight:bold'>{high_text}</td>"
            f"<td style='padding:8px;border:1px solid #ddd;text-align:center'>{sparkline}</td>"
            f"</tr>"
        )
        row_idx += 1

    store_headers = "".join(
        f"<th style='padding:8px;border:1px solid #ddd;background:#f2f2f2'>{s.title()}</th>"
        for s in all_stores
    )

    if static_mode:
        run_btn_html = (
            "<p style='font-size:13px;color:#888;margin-bottom:18px'>"
            "&#8987; Prices update automatically 4&times; daily via GitHub Actions.</p>"
        )
    else:
        run_btn_html = (
            "<button id='run-btn' onclick='runNow(this)'>Run Now</button>"
            "<span id='run-status'></span>"
        )

    dashboard_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Price Tracker Dashboard</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    margin: 28px;
    color: #333;
    background: #fff;
  }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  .subtitle {{ color: #888; font-size: 13px; margin-bottom: 16px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th {{ text-align: left; white-space: nowrap; }}
  tr:hover td {{ background: #fafafa; }}
  .legend {{ margin-top: 14px; font-size: 12px; color: #888; }}
  .fresh-key {{
    display: inline-block;
    background: #fffde7;
    border: 1px solid #f39c12;
    padding: 1px 6px;
    border-radius: 3px;
    font-size: 12px;
  }}
  #run-btn {{
    padding: 7px 18px;
    font-size: 13px;
    background: #4a90d9;
    color: #fff;
    border: none;
    border-radius: 5px;
    cursor: pointer;
    margin-bottom: 18px;
  }}
  #run-btn:disabled {{ background: #aaa; cursor: default; }}
  #run-status {{ font-size: 13px; color: #555; margin-left: 10px; }}
</style>
</head>
<body>
<h1>Price Tracker Dashboard</h1>
<p class="subtitle">
  Last updated: {datetime.now().strftime("%Y-%m-%d %H:%M")}
  &nbsp;|&nbsp;
  <span class="fresh-key">highlighted</span> = fetched today
</p>
{run_btn_html}
<table>
  <thead>
    <tr style="background:#f2f2f2">
      <th style="padding:8px;border:1px solid #ddd">Product</th>
      <th style="padding:8px;border:1px solid #ddd;text-align:center">Target</th>
      <th style="padding:8px;border:1px solid #ddd;text-align:center">MSRP</th>
      {store_headers}
      <th style="padding:8px;border:1px solid #ddd;text-align:center">30d Low</th>
      <th style="padding:8px;border:1px solid #ddd;text-align:center">30d High</th>
      <th style="padding:8px;border:1px solid #ddd;text-align:center">Trend (30d)</th>
    </tr>
  </thead>
  <tbody>
    {rows_html}
  </tbody>
</table>
<p class="legend">
  <span style="color:#27ae60;font-weight:bold">Green price</span> = below target
  &nbsp;|&nbsp;
  Stale dates shown below price when a fetch failed on the last run
  &nbsp;|&nbsp;
  30d Low / High = across all sources
  &nbsp;|&nbsp;
  % MSRP shown per-source when MSRP is configured
</p>
<script>
function onTargetChange(input) {{
  var wrap = input.closest('.target-wrap');
  wrap.querySelector('.save-btn').style.display = 'inline-block';
  wrap.querySelector('.save-ok').style.display = 'none';
  input.style.borderColor = '#ccc';
}}

function saveTarget(btn) {{
  var wrap = btn.closest('.target-wrap');
  var input = wrap.querySelector('.target-input');
  var ok = wrap.querySelector('.save-ok');
  var name = input.getAttribute('data-name');
  var price = parseFloat(input.value);
  if (isNaN(price) || price <= 0) return;
  btn.disabled = true;
  btn.textContent = '...';
  fetch('http://127.0.0.1:8080/update-target', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{name: name, target_price: price}})
  }})
  .then(function(r) {{ return r.json(); }})
  .then(function(d) {{
    btn.style.display = 'none';
    btn.textContent = 'Save';
    btn.disabled = false;
    if (d.ok) {{
      input.style.borderColor = '#27ae60';
      ok.style.display = 'inline';
      setTimeout(function() {{
        input.style.borderColor = '#ccc';
        ok.style.display = 'none';
      }}, 2000);
    }} else {{
      input.style.borderColor = '#e74c3c';
      setTimeout(function() {{ input.style.borderColor = '#ccc'; }}, 2000);
    }}
  }})
  .catch(function() {{
    btn.textContent = 'Save';
    btn.disabled = false;
    input.style.borderColor = '#e74c3c';
    setTimeout(function() {{ input.style.borderColor = '#ccc'; }}, 2000);
  }});
}}

function runNow(btn) {{
  btn.disabled = true;
  var status = document.getElementById('run-status');
  status.textContent = 'Starting...';
  fetch('http://127.0.0.1:8080/run-now', {{method: 'POST'}})
    .then(function(r) {{ return r.json(); }})
    .then(function(d) {{
      status.textContent = d.message;
      if (d.ok) {{
        var countdown = 35;
        var timer = setInterval(function() {{
          countdown--;
          status.textContent = d.message + ' (refreshing in ' + countdown + 's)';
          if (countdown <= 0) {{
            clearInterval(timer);
            location.reload();
          }}
        }}, 1000);
      }} else {{
        btn.disabled = false;
      }}
    }})
    .catch(function() {{
      status.textContent = 'Could not connect — is server.py running? (python server.py)';
      btn.disabled = false;
    }});
}}
</script>
</body>
</html>"""

    DASHBOARD_FILE.write_text(dashboard_html)
    log.info("Dashboard written to %s", DASHBOARD_FILE)


# ---------------------------------------------------------------------------
# Email alert
# ---------------------------------------------------------------------------

def send_alert(config: dict, alerts: list[dict]) -> None:
    email_cfg = config["email"]
    sender = email_cfg["sender"]
    recipient = email_cfg["recipient"]
    password = email_cfg["password"]
    if not password:
        log.warning("No email password configured — skipping alert email.")
        return

    scraped = [a for a in alerts if a["store"] != "camelcamelcamel"]
    ccc = [a for a in alerts if a["store"] == "camelcamelcamel"]

    subject = f"Price Alert: {len(alerts)} item(s) below target"

    # --- Scraped alerts table ---
    scraped_section = ""
    if scraped:
        rows = ""
        for a in scraped:
            price_text = f"${a['current_price']:.2f}" if a["current_price"] is not None else "—"
            target_text = f"${a['target_price']:.2f}" if a["target_price"] is not None else "—"
            rows += (
                f"<tr>"
                f"<td style='padding:8px;border:1px solid #ddd'>{a['name']}</td>"
                f"<td style='padding:8px;border:1px solid #ddd'>{a['store'].title()}</td>"
                f"<td style='padding:8px;border:1px solid #ddd;color:green'><b>{price_text}</b></td>"
                f"<td style='padding:8px;border:1px solid #ddd'>{target_text}</td>"
                f"<td style='padding:8px;border:1px solid #ddd'><a href='{a['url']}'>View</a></td>"
                f"</tr>"
            )
        scraped_section = f"""
        <h3 style='margin-top:24px'>Items below your target price</h3>
        <table style='border-collapse:collapse;width:100%'>
          <thead>
            <tr style='background:#f2f2f2'>
              <th style='padding:8px;border:1px solid #ddd'>Product</th>
              <th style='padding:8px;border:1px solid #ddd'>Store</th>
              <th style='padding:8px;border:1px solid #ddd'>Current Price</th>
              <th style='padding:8px;border:1px solid #ddd'>Your Target</th>
              <th style='padding:8px;border:1px solid #ddd'>Link</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>"""

    # --- CamelCamelCamel alerts table ---
    ccc_section = ""
    if ccc:
        rows = ""
        for a in ccc:
            price_text = f"${a['current_price']:.2f}" if a["current_price"] is not None else "See link"
            rows += (
                f"<tr>"
                f"<td style='padding:8px;border:1px solid #ddd'>{a['name']}</td>"
                f"<td style='padding:8px;border:1px solid #ddd;color:green'><b>{price_text}</b></td>"
                f"<td style='padding:8px;border:1px solid #ddd'><a href='{a['url']}'>View on CamelCamelCamel</a></td>"
                f"</tr>"
            )
        ccc_section = f"""
        <h3 style='margin-top:24px'>CamelCamelCamel price watch alerts</h3>
        <table style='border-collapse:collapse;width:100%'>
          <thead>
            <tr style='background:#f2f2f2'>
              <th style='padding:8px;border:1px solid #ddd'>Product</th>
              <th style='padding:8px;border:1px solid #ddd'>Price</th>
              <th style='padding:8px;border:1px solid #ddd'>Link</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>"""

    email_html = f"""
    <html><body>
    <h2>Price Drop Alert</h2>
    {scraped_section}
    {ccc_section}
    <p style='color:#888;font-size:12px'>Checked on {datetime.now().strftime("%Y-%m-%d %H:%M")}</p>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(email_html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, recipient, msg.as_string())
        log.info("Alert email sent to %s (%d items)", recipient, len(alerts))
    except smtplib.SMTPAuthenticationError:
        log.error(
            "Gmail authentication failed. Make sure you are using an App Password, "
            "not your regular Gmail password. See: https://myaccount.google.com/apppasswords"
        )
    except Exception as e:
        log.error("Failed to send email: %s", e)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> None:
    config = load_config()
    history = load_history()
    products = config.get("products", [])

    if not products:
        log.warning("No products defined in config.yaml")
        return

    alerts = []
    today = datetime.now().strftime("%Y-%m-%d")

    for product in products:
        name = product["name"]
        url = product["url"]
        target = float(product["target_price"])
        store = product.get("store", "unknown")

        log.info("Checking %s (%s)...", name, store)
        current_price, in_stock, msrp = fetch_price(product)

        if current_price is None:
            log.warning("Skipping %s — could not fetch price", name)
            continue

        log.info("  %s: $%.2f (target $%.2f, msrp $%.2f) in_stock=%s", name, current_price, target, msrp or 0, in_stock)

        if url not in history:
            history[url] = []
        history[url].append({"date": today, "price": current_price, "in_stock": in_stock, "msrp": msrp})
        history[url] = history[url][-90:]

        if current_price < target:
            log.info("  BELOW TARGET — adding to alert list")
            alerts.append({
                "name": name,
                "url": url,
                "store": store,
                "current_price": current_price,
                "target_price": target,
            })

    save_history(history)

    # CamelCamelCamel RSS alerts
    seen_ids = load_camel_seen()
    ccc_alerts = fetch_camelcamelcamel_alerts(config, seen_ids)
    if ccc_alerts:
        for a in ccc_alerts:
            seen_ids.add(a["entry_id"])
        save_camel_seen(seen_ids)
        alerts.extend(ccc_alerts)

    generate_dashboard(config, history)

    if alerts:
        send_alert(config, alerts)
    else:
        log.info("No prices below target today.")


if __name__ == "__main__":
    run()
