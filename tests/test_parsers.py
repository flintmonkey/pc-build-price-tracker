"""Unit tests for HTML price parsers."""

from pathlib import Path

from bs4 import BeautifulSoup

from providers.parsing import detect_stock, parse_price, price_from_json_ld

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_price_dollar_amount():
    assert parse_price("$449.99") == 449.99
    assert parse_price("1,299.00") == 1299.0


def test_parse_price_integer():
    assert parse_price("$99") == 99.0


def test_detect_stock_in_stock():
    soup = BeautifulSoup("<html><body>In stock ready to ship</body></html>", "html.parser")
    assert detect_stock(soup, 99.0) is True


def test_detect_stock_out_of_stock():
    soup = BeautifulSoup("<html><body>Sold out</body></html>", "html.parser")
    assert detect_stock(soup, 99.0) is False


def test_price_from_json_ld():
    html = """
    <script type="application/ld+json">
    {"@type":"Product","offers":{"price":"599.99","availability":"InStock"}}
    </script>
    """
    soup = BeautifulSoup(html, "html.parser")
    assert price_from_json_ld(soup) == 599.99


def test_newegg_fixture_price():
    path = FIXTURES / "newegg_sample.html"
    if not path.exists():
        return
    soup = BeautifulSoup(path.read_text(), "html.parser")
    el = soup.select_one("div.price-new-right .price-current")
    assert el is not None
    price = parse_price(el.get_text(strip=True))
    assert price is not None
    assert price > 0
