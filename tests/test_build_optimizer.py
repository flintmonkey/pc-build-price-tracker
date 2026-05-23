"""Tests for build cart optimization."""

from build_optimizer import cheapest_cart, group_products, single_vendor_cart


def _sample_config():
    products = [
        {"name": "CPU", "store": "newegg", "url": "https://newegg.com/cpu", "target_price": 400},
        {"name": "CPU", "store": "amazon", "url": "https://amazon.com/cpu", "target_price": 400},
        {"name": "GPU", "store": "newegg", "url": "https://newegg.com/gpu", "target_price": 1000},
        {"name": "GPU", "store": "amazon", "url": "https://amazon.com/gpu", "target_price": 1000},
    ]
    return products


def test_cheapest_cart_picks_lowest_per_part():
    products = _sample_config()
    groups = group_products(products)
    history = {
        "https://newegg.com/cpu": [{"date": "2026-05-01", "price": 450.0, "in_stock": True}],
        "https://amazon.com/cpu": [{"date": "2026-05-01", "price": 420.0, "in_stock": True}],
        "https://newegg.com/gpu": [{"date": "2026-05-01", "price": 1100.0, "in_stock": True}],
        "https://amazon.com/gpu": [{"date": "2026-05-01", "price": 1050.0, "in_stock": True}],
    }
    plan = cheapest_cart(groups, history)
    assert plan.total == 420.0 + 1050.0
    assert plan.vendor_count == 1
    assert all(line.store == "amazon" for line in plan.lines)


def test_single_vendor_cart():
    products = _sample_config()
    groups = group_products(products)
    history = {
        "https://newegg.com/cpu": [{"date": "2026-05-01", "price": 450.0, "in_stock": True}],
        "https://amazon.com/cpu": [{"date": "2026-05-01", "price": 420.0, "in_stock": True}],
        "https://newegg.com/gpu": [{"date": "2026-05-01", "price": 1000.0, "in_stock": True}],
        "https://amazon.com/gpu": [{"date": "2026-05-01", "price": 1050.0, "in_stock": True}],
    }
    plan = single_vendor_cart("newegg", groups, history)
    assert plan.total == 1450.0
    assert not plan.missing_parts
