"""HTTP clients shared by scrape providers."""

import cloudscraper
import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_scrapers: dict[str, cloudscraper.CloudScraper] = {}


def get_scraper(key: str) -> cloudscraper.CloudScraper:
    if key not in _scrapers:
        _scrapers[key] = cloudscraper.create_scraper()
    return _scrapers[key]
