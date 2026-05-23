"""Tests for provider helpers."""

from providers.base import extract_asin, extract_bestbuy_sku, price_source_for


def test_extract_asin_from_dp_url():
    product = {"url": "https://www.amazon.com/dp/B0G8JMLXNQ"}
    assert extract_asin(product) == "B0G8JMLXNQ"


def test_extract_asin_explicit():
    product = {"url": "https://amazon.com/foo", "asin": "B012345678"}
    assert extract_asin(product) == "B012345678"


def test_extract_bestbuy_sku_from_path():
    product = {"url": "https://www.bestbuy.com/site/foo/6498482.p"}
    assert extract_bestbuy_sku(product) == "6498482"


def test_extract_bestbuy_sku_from_sku_segment():
    product = {"url": "https://www.bestbuy.com/product/foo/J36XJSZK9Z/sku/11082031"}
    assert extract_bestbuy_sku(product) == "11082031"


def test_price_source_defaults_to_scrape():
    config = {"stores": {"amazon": {"price_source": "api"}}}
    product = {"store": "newegg"}
    assert price_source_for(product, "newegg", config) == "scrape"


def test_price_source_per_store():
    config = {"stores": {"amazon": {"price_source": "api"}}}
    product = {"store": "amazon"}
    assert price_source_for(product, "amazon", config) == "api"
