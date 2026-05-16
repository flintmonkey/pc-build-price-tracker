# PC Parts Price Tracker — Project Requirements

## 1. Project Overview

A personal, locally-run Python application that monitors PC component prices across multiple online retailers, maintains a price history, and notifies the user via email when prices drop below predefined target thresholds. A local web dashboard provides a real-time price comparison view with trend data.

---

## 2. Functional Requirements

### 2.1 Price Monitoring

| ID | Requirement |
|----|-------------|
| FR-01 | The system shall fetch current prices for each configured product from its configured store URL |
| FR-02 | The system shall support the following stores: Newegg, Amazon, NVIDIA Marketplace, B&H Photo, and Microcenter (Cambridge/Boston, store ID 025) |
| FR-03 | The system shall attempt multiple HTML parsing strategies per store before marking a price as unavailable |
| FR-04 | The system shall detect whether each product is In Stock, Out of Stock, or Unknown at the time of each check |
| FR-05 | A failed price fetch shall log a warning and skip that product without crashing the rest of the run |

### 2.2 Price History

| ID | Requirement |
|----|-------------|
| FR-06 | The system shall record the date, price, and stock status for every successful fetch |
| FR-07 | Price history shall be retained for a rolling 90-day window per product URL |
| FR-08 | History shall be stored locally in `prices_history.json` and persist between runs |
| FR-09 | History from before the stock-status feature was added shall continue to load without errors |

### 2.3 Email Alerts

| ID | Requirement |
|----|-------------|
| FR-10 | The system shall send a single digest email when one or more products are priced below their target on a given run |
| FR-11 | The alert email shall include: product name, store, current price, target price, and a direct link to the product page |
| FR-12 | No email shall be sent when no products are below their target price |
| FR-13 | Email shall be sent via Gmail SMTP using an App Password (not a regular Gmail password) |
| FR-14 | Authentication failures shall log a clear error message with a link to generate an App Password |

### 2.4 Dashboard

| ID | Requirement |
|----|-------------|
| FR-15 | The system shall generate a `dashboard.html` file after every run |
| FR-16 | The dashboard shall group products by name and show one column per store |
| FR-17 | Each store cell shall display: current price, stock status badge, and a stale-data date label if the last successful fetch was not today |
| FR-18 | Prices below the target shall be highlighted in green |
| FR-19 | Store cells fetched on the current day shall be visually highlighted to distinguish fresh vs. stale data |
| FR-20 | The dashboard shall display the 30-day low and 30-day high price across all stores for each product |
| FR-21 | The dashboard shall display a 30-day sparkline trend chart for each product |
| FR-22 | The dashboard shall include a "Run Now" button that triggers an on-demand price check |

### 2.5 Local Web Server

| ID | Requirement |
|----|-------------|
| FR-23 | `server.py` shall serve the dashboard at `http://127.0.0.1:5000` |
| FR-24 | The server shall expose a `POST /run-now` endpoint that triggers `price_tracker.run()` in a background thread |
| FR-25 | If a price check is already in progress, `POST /run-now` shall return a 409 response with an explanatory message |
| FR-26 | If `dashboard.html` does not yet exist when the server starts, an empty dashboard shall be auto-generated |
| FR-27 | The server is optional — the tracker shall function fully when run directly from the command line without the server |

### 2.6 Scheduling

| ID | Requirement |
|----|-------------|
| FR-28 | `setup_cron.sh` shall install a cron job that runs `price_tracker.py` daily at 9:00 AM local time |
| FR-29 | The cron job shall append its output to `cron.log` |
| FR-30 | Running `setup_cron.sh` more than once shall not create duplicate cron entries |

---

## 3. Configuration Requirements

| ID | Requirement |
|----|-------------|
| CR-01 | All user-configurable settings shall live in `config.yaml` — no hardcoded values in Python files |
| CR-02 | Email sender, recipient, and App Password shall be configurable in `config.yaml` |
| CR-03 | Each product entry shall specify: name, URL, target price, and store |
| CR-04 | Multiple entries with the same `name` field but different stores shall be grouped together in the dashboard |
| CR-05 | Adding or removing a product shall require only editing `config.yaml` — no code changes |
| CR-06 | Supported store values: `newegg`, `amazon`, `nvidia`, `bhphoto`, `microcenter` |

---

## 4. Technical Requirements

| ID | Requirement |
|----|-------------|
| TR-01 | The application shall run on Python 3.10 or higher |
| TR-02 | Dependencies: `requests`, `beautifulsoup4`, `lxml`, `PyYAML`, `flask` |
| TR-03 | No browser automation (Selenium, Playwright) — all fetching via `requests` + `BeautifulSoup` |
| TR-04 | The Microcenter scraper shall pass `storeSelected=025` as a cookie to pin pricing to the Cambridge/Boston store |
| TR-05 | All scraper functions shall return a `(price, in_stock)` tuple — `None` for either field indicates the value could not be determined |
| TR-06 | All HTTP requests shall use a realistic browser User-Agent string and a 15-second timeout |
| TR-07 | Logging shall write to both `price_tracker.log` (file) and stdout simultaneously |
| TR-08 | The dashboard HTML file shall be self-contained — no external CDN dependencies, no build step |

---

## 5. Known Limitations

| Limitation | Detail |
|---|---|
| Amazon anti-bot detection | Amazon actively detects and blocks scrapers. Prices may return N/A intermittently. No fix is guaranteed without using a paid proxy or the Amazon Product Advertising API. |
| NVIDIA Marketplace | The NVIDIA Marketplace page structure may not expose price data in standard HTML — the scraper uses best-effort fallbacks. |
| Cron requires the machine to be on | The scheduled 9 AM job will not fire if the computer is asleep or off. |
| Static dashboard | `dashboard.html` is only as fresh as the last run. It does not auto-update without the server running. |
| No price-drop history alerts | The tracker only alerts when the current price is below target — it does not alert on percentage drops or sudden price changes. |

---

## 6. Products Currently Tracked

| Product | Target Price | Stores |
|---|---|---|
| AMD Ryzen 7 9800X3D | $419.99 | Newegg, Amazon |
| MSI MEG X870E ACE MAX | $649.99 | Newegg, Amazon |
| G.Skill Trident Z5 Neo RGB 32GB DDR5-6000 | $449.99 | Newegg, Amazon |
| ASUS TUF Gaming RTX 5080 OC | $1,299.99 | Newegg, Amazon, NVIDIA |
| Fractal Design Torrent RGB | $169.99 | Newegg, Amazon |
| Noctua NH-D15 G2 chromax.black | $149.99 | Newegg, Amazon |
| Windows 11 Home Retail | $109.99 | Newegg |

*Microcenter URLs to be added manually via `config.yaml` — see README.md for instructions.*

---

## 7. File Output Summary

| File | Created by | Purpose |
|---|---|---|
| `dashboard.html` | `price_tracker.py` on every run | Browser dashboard |
| `prices_history.json` | `price_tracker.py` on every run | Persistent price history |
| `price_tracker.log` | `price_tracker.py` on every run | Debug and audit log |
| `cron.log` | Cron job | Scheduled run output |
