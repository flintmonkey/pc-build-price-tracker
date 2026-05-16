# PC Parts Price Tracker

Monitors prices for your PC parts across Newegg, Amazon, NVIDIA Marketplace, and Microcenter (Boston). Sends you a Gmail alert when any price drops below your target, and gives you a local dashboard to view prices and trends at a glance.

---

## What's in this folder

| File | What it does |
|---|---|
| `price_tracker.py` | The main script — fetches prices, saves history, sends email alerts |
| `server.py` | Runs a local web server so you can view the dashboard in your browser |
| `config.yaml` | Your product list and email settings — edit this to add/remove products |
| `requirements.txt` | The Python packages this project needs |
| `setup_cron.sh` | One-time script to schedule automatic daily price checks at 9 AM |
| `dashboard.html` | Auto-generated dashboard — created after the first run, open in any browser |
| `prices_history.json` | Auto-generated price history — do not edit manually |
| `price_tracker.log` | Auto-generated log file — useful for troubleshooting |

---

## First-time setup

### Step 1 — Open Terminal

Press **Cmd + Space**, type **Terminal**, and press **Enter**.

### Step 2 — Navigate to the project folder

Copy and paste this line exactly, then press Enter:

```
cd /Users/kimberlycraven/Documents/Claude/Projects/PriceTracker
```

You should see the prompt change to show the folder name. You only need to do this once per Terminal session.

### Step 3 — Install dependencies

Copy and paste this line, then press Enter:

```
python3 -m pip install -r requirements.txt
```

This downloads the Python packages the tracker needs. You only need to do this **once** (or again if you update `requirements.txt`).

---

## Running the dashboard

### Option A — View the dashboard in your browser (recommended)

1. Open Terminal and navigate to the project folder (Step 2 above)
2. Run:
   ```
   python3 server.py
   ```
3. Open your browser and go to: **http://localhost:5000**
4. Click **Run Now** to trigger a fresh price check
5. The page will automatically refresh when the check is done (~30 seconds)
6. When you're done, press **Ctrl + C** in the Terminal to stop the server

### Option B — Run a price check without the dashboard

```
python3 price_tracker.py
```

This fetches all prices, updates history, generates `dashboard.html`, and sends an email if anything is below target.

---

## Setting up automatic daily checks (recommended)

Run this once to schedule the tracker to run every day at 9 AM automatically:

```
bash setup_cron.sh
```

After that, you don't need to run anything manually — the tracker will check prices on its own every morning and email you if something drops.

> **Note:** Your computer needs to be on and awake at 9 AM for the cron job to fire.

---

## Adding a Microcenter product

1. Go to [microcenter.com](https://www.microcenter.com) and find the product page
2. Copy the URL from your browser (it looks like `https://www.microcenter.com/product/123456/product-name`)
3. Open `config.yaml` in a text editor and add an entry like this:

```yaml
  - name: "AMD Ryzen 7 9800X3D"
    url: "https://www.microcenter.com/product/PASTE_URL_HERE"
    target_price: 419.99
    store: "microcenter"
```

The Cambridge/Boston store (store ID 025) is used automatically — you don't need to do anything special for that.

---

## Adding or removing any product

Open `config.yaml` in a text editor. Each product entry looks like this:

```yaml
  - name: "Product Name"
    url: "https://store.com/product-page"
    target_price: 199.99
    store: "newegg"   # options: newegg, amazon, nvidia, microcenter, bhphoto
```

- **name** — what shows up in the dashboard and alert emails (use the same name across stores to group them)
- **url** — the direct product page URL (not a search page)
- **target_price** — you'll get an email alert when the price drops below this
- **store** — must be one of the supported values listed above

To remove a product, delete its entire 4-line block from `config.yaml`.

---

## Troubleshooting

**"command not found" when running python3**
Your system Python isn't set up. Try `python3 --version` — if that fails, install Python from [python.org](https://www.python.org/downloads/).

**"No module named flask" or similar**
Run the install step again: `python3 -m pip install -r requirements.txt`

**"Could not connect — is server.py running?"**
The Run Now button only works when `server.py` is running. Start it with `python3 server.py` first.

**Amazon prices showing N/A**
Amazon actively blocks scrapers. If it happens consistently, try running again later — Amazon sometimes temporarily blocks requests and recovers on its own.

**Gmail authentication failed**
Make sure the password in `config.yaml` is a Gmail App Password (not your regular Gmail password). Generate one at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) — 2FA must be enabled on your Google account first.

**dashboard.html doesn't exist yet**
It's created automatically after the first run. Click Run Now in the browser, or run `python3 price_tracker.py` once.
