#!/usr/bin/env python3
"""Daily price tracker for PC parts across Newegg, B&H Photo, Amazon, and Microcenter."""

import json
import logging
import os
import smtplib
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import html
import urllib.request

import certifi
import feedparser
import yaml

from build_optimizer import cheapest_cart, group_products, render_build_summary_html
from providers import fetch_price
from providers.parsing import filter_anomalous_lows, parse_price

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

def _load_dotenv() -> None:
    """Load .env into os.environ without overwriting existing vars."""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def load_config() -> dict:
    _load_dotenv()
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


def _check_product(product: dict, config: dict, today: str) -> tuple[dict, dict | None, dict | None]:
    """Fetch one product; return (product, history_entry or None, alert or None)."""
    name = product["name"]
    url = product["url"]
    target = float(product["target_price"])
    store = product.get("store", "unknown")

    log.info("Checking %s (%s)...", name, store)
    current_price, in_stock, msrp = fetch_price(product, config)

    if current_price is None:
        log.warning("Skipping %s — could not fetch price", name)
        return product, None, None

    log.info(
        "  %s: $%.2f (target $%.2f, msrp $%.2f) in_stock=%s",
        name, current_price, target, msrp or 0, in_stock,
    )

    entry = {"date": today, "price": current_price, "in_stock": in_stock, "msrp": msrp}
    alert = None
    if current_price < target:
        log.info("  BELOW TARGET — adding to alert list")
        alert = {
            "name": name,
            "url": url,
            "store": store,
            "current_price": current_price,
            "target_price": target,
        }
    return product, entry, alert


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
        price = parse_price(title) or parse_price(summary)

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
        all_prices_flat = filter_anomalous_lows(all_prices_flat)

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
        spark_prices = filter_anomalous_lows(spark_prices)
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

    build_summary_html = render_build_summary_html(products, history)

    if static_mode:
        run_btn_html = (
            "<div style='margin-bottom:18px;display:flex;align-items:center;gap:12px;flex-wrap:wrap'>"
            "<span style='font-size:13px;color:#888'>"
            "&#8987; Prices update automatically 4&times; daily via GitHub Actions.</span>"
            "<button onclick='showTokenModal()' "
            "style='font-size:13px;padding:6px 14px;background:#555;color:#fff;"
            "border:none;border-radius:5px;cursor:pointer;white-space:nowrap'>"
            "&#9881; GitHub Token</button>"
            "<span id='token-status' style='font-size:12px'></span>"
            "</div>"
            "<div id='token-modal' style='display:none;position:fixed;top:0;left:0;"
            "width:100%;height:100%;background:rgba(0,0,0,0.5);z-index:999;"
            "align-items:center;justify-content:center'>"
            "<div style='background:#fff;padding:24px;border-radius:8px;max-width:460px;"
            "width:90%;box-shadow:0 4px 20px rgba(0,0,0,0.3)'>"
            "<h3 style='margin:0 0 8px;font-size:16px'>GitHub Personal Access Token</h3>"
            "<p style='font-size:12px;color:#666;margin:0 0 12px'>Fine-grained PAT with "
            "<strong>Contents: Read &amp; Write</strong> for this repo. "
            "Stored only in your browser&#39;s localStorage.</p>"
            "<input id='token-input' type='password' placeholder='ghp_...' "
            "style='width:100%;box-sizing:border-box;padding:8px;border:1px solid #ccc;"
            "border-radius:4px;font-size:13px;margin-bottom:12px'>"
            "<div style='display:flex;gap:8px;justify-content:flex-end'>"
            "<button onclick='clearToken()' style='padding:6px 14px;background:#e74c3c;"
            "color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:13px'>Clear</button>"
            "<button onclick='closeTokenModal()' style='padding:6px 14px;background:#aaa;"
            "color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:13px'>Cancel</button>"
            "<button onclick='saveTokenFromModal()' style='padding:6px 14px;background:#4a90d9;"
            "color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:13px'>Save</button>"
            "</div></div></div>"
        )
    else:
        run_btn_html = (
            "<button id='run-btn' onclick='runNow(this)'>Run Now</button>"
            "<span id='run-status'></span>"
        )

    static_js_var = "true" if static_mode else "false"
    js_section = "<script>\nvar STATIC_MODE = " + static_js_var + ";\n" + r"""
function onTargetChange(input) {
  var wrap = input.closest('.target-wrap');
  wrap.querySelector('.save-btn').style.display = 'inline-block';
  wrap.querySelector('.save-ok').style.display = 'none';
  input.style.borderColor = '#ccc';
}

function saveTarget(btn) {
  var wrap = btn.closest('.target-wrap');
  var input = wrap.querySelector('.target-input');
  var ok = wrap.querySelector('.save-ok');
  var name = input.getAttribute('data-name');
  var price = parseFloat(input.value);
  if (isNaN(price) || price <= 0) return;
  btn.disabled = true;
  btn.textContent = '...';
  if (STATIC_MODE) {
    saveTargetGitHub(btn, input, ok, name, price);
  } else {
    saveTargetLocal(btn, input, ok, name, price);
  }
}

function saveTargetLocal(btn, input, ok, name, price) {
  fetch('http://127.0.0.1:8080/update-target', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: name, target_price: price})
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    btn.style.display = 'none';
    btn.textContent = 'Save';
    btn.disabled = false;
    if (d.ok) {
      input.style.borderColor = '#27ae60';
      ok.style.display = 'inline';
      setTimeout(function() {
        input.style.borderColor = '#ccc';
        ok.style.display = 'none';
      }, 2000);
    } else {
      input.style.borderColor = '#e74c3c';
      setTimeout(function() { input.style.borderColor = '#ccc'; }, 2000);
    }
  })
  .catch(function() {
    btn.textContent = 'Save';
    btn.disabled = false;
    input.style.borderColor = '#e74c3c';
    setTimeout(function() { input.style.borderColor = '#ccc'; }, 2000);
  });
}

async function saveTargetGitHub(btn, input, ok, name, price) {
  var token = localStorage.getItem('gh_pat');
  if (!token) {
    btn.textContent = 'Save';
    btn.disabled = false;
    showTokenModal();
    return;
  }
  try {
    var apiUrl = 'https://api.github.com/repos/flintmonkey/pc-build-price-tracker/contents/config.yaml';
    var getRes = await fetch(apiUrl, {
      headers: {'Authorization': 'token ' + token, 'Accept': 'application/vnd.github.v3+json'}
    });
    if (!getRes.ok) throw new Error('GitHub API error: ' + getRes.status);
    var fileData = await getRes.json();
    var currentContent = atob(fileData.content.replace(/\s/g, ''));
    var updatedContent = updateConfigYaml(currentContent, name, price);
    if (updatedContent === currentContent) throw new Error('Product not found in config.yaml');
    var putRes = await fetch(apiUrl, {
      method: 'PUT',
      headers: {
        'Authorization': 'token ' + token,
        'Accept': 'application/vnd.github.v3+json',
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        message: 'Update target for ' + name + ' to $' + price.toFixed(2),
        content: btoa(unescape(encodeURIComponent(updatedContent))),
        sha: fileData.sha
      })
    });
    if (!putRes.ok) {
      var err = await putRes.json().catch(function() { return {}; });
      throw new Error(err.message || 'Update failed: ' + putRes.status);
    }
    btn.style.display = 'none';
    btn.textContent = 'Save';
    btn.disabled = false;
    input.style.borderColor = '#27ae60';
    ok.style.display = 'inline';
    setTimeout(function() {
      input.style.borderColor = '#ccc';
      ok.style.display = 'none';
    }, 3000);
  } catch(e) {
    btn.textContent = 'Save';
    btn.disabled = false;
    input.style.borderColor = '#e74c3c';
    setTimeout(function() { input.style.borderColor = '#ccc'; }, 3000);
    alert('Save failed: ' + e.message);
  }
}

function updateConfigYaml(content, productName, newPrice) {
  var lines = content.split('\n');
  var inProduct = false;
  var result = [];
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i];
    if (line.trimStart().startsWith('- ')) inProduct = false;
    if (line.indexOf('name:') !== -1 && line.indexOf(productName) !== -1) inProduct = true;
    if (inProduct && line.trim().startsWith('target_price:')) {
      var indent = line.match(/^(\s*)/)[1];
      result.push(indent + 'target_price: ' + newPrice.toFixed(2));
      inProduct = false;
    } else {
      result.push(line);
    }
  }
  return result.join('\n');
}

function showTokenModal() {
  var modal = document.getElementById('token-modal');
  modal.style.display = 'flex';
  var existing = localStorage.getItem('gh_pat');
  document.getElementById('token-input').value = existing || '';
  updateTokenStatus();
}

function closeTokenModal() {
  document.getElementById('token-modal').style.display = 'none';
}

function saveTokenFromModal() {
  var val = document.getElementById('token-input').value.trim();
  if (val) {
    localStorage.setItem('gh_pat', val);
    updateTokenStatus();
    closeTokenModal();
  }
}

function clearToken() {
  localStorage.removeItem('gh_pat');
  document.getElementById('token-input').value = '';
  updateTokenStatus();
  closeTokenModal();
}

function updateTokenStatus() {
  var statusEl = document.getElementById('token-status');
  if (!statusEl) return;
  var token = localStorage.getItem('gh_pat');
  statusEl.textContent = token ? '✓ Token set' : 'No token — click to set';
  statusEl.style.color = token ? '#27ae60' : '#e74c3c';
}

window.addEventListener('load', function() {
  if (STATIC_MODE) updateTokenStatus();
});

function runNow(btn) {
  btn.disabled = true;
  var status = document.getElementById('run-status');
  status.textContent = 'Starting...';
  fetch('http://127.0.0.1:8080/run-now', {method: 'POST'})
    .then(function(r) { return r.json(); })
    .then(function(d) {
      status.textContent = d.message;
      if (d.ok) {
        var countdown = 35;
        var timer = setInterval(function() {
          countdown--;
          status.textContent = d.message + ' (refreshing in ' + countdown + 's)';
          if (countdown <= 0) {
            clearInterval(timer);
            location.reload();
          }
        }, 1000);
      } else {
        btn.disabled = false;
      }
    })
    .catch(function() {
      status.textContent = 'Could not connect — is server.py running? (python server.py)';
      btn.disabled = false;
    });
}
</script>"""

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
{build_summary_html}
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
{js_section}
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

    build_note = ""
    products = config.get("products", [])
    if products:
        history = load_history()
        mix = cheapest_cart(group_products(products), history)
        if mix.lines:
            build_note = (
                f"<p style='font-size:14px;margin:16px 0'>"
                f"<strong>Cheapest full build (in-stock mix):</strong> ${mix.total:.2f} "
                f"across {mix.vendor_count} vendor(s)</p>"
            )

    email_html = f"""
    <html><body>
    <h2>Price Drop Alert</h2>
    {build_note}
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

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(_check_product, product, config, today): product
            for product in products
        }
        for future in as_completed(futures):
            product, entry, alert = future.result()
            url = product["url"]
            if entry is None:
                continue
            if url not in history:
                history[url] = []
            history[url].append(entry)
            history[url] = history[url][-90:]
            if alert:
                alerts.append(alert)

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
