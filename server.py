#!/usr/bin/env python3
"""
Local dashboard server for the price tracker.

Usage:
    python server.py

Then open http://127.0.0.1:8080 in your browser.
The "Run Now" button on the dashboard will trigger a fresh price check.
"""

import threading
from pathlib import Path

from ruamel.yaml import YAML
from flask import Flask, jsonify, make_response, request, send_file

import price_tracker

app = Flask(__name__)
BASE_DIR = Path(__file__).parent

_running = False
_lock = threading.Lock()


@app.get("/")
def index():
    config = price_tracker.load_config()
    history = price_tracker.load_history()
    price_tracker.generate_dashboard(config, history)
    resp = make_response(send_file(BASE_DIR / "dashboard.html"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp


@app.post("/run-now")
def run_now():
    global _running
    with _lock:
        if _running:
            return jsonify({"ok": False, "message": "A price check is already running — please wait."})
        _running = True

    def _task():
        global _running
        try:
            price_tracker.run()
        finally:
            with _lock:
                _running = False

    threading.Thread(target=_task, daemon=True).start()
    return jsonify({"ok": True, "message": "Price check started."})


@app.post("/update-target")
def update_target():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    try:
        new_target = round(float(data.get("target_price", 0)), 2)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "message": "Invalid price value"})

    if not name or new_target <= 0:
        return jsonify({"ok": False, "message": "Invalid input"})

    cfg_path = BASE_DIR / "config.yaml"
    yaml_parser = YAML()
    yaml_parser.preserve_quotes = True
    with open(cfg_path) as f:
        cfg = yaml_parser.load(f)

    updated = sum(
        1 for p in cfg.get("products", [])
        if p.get("name") == name and not p.update({"target_price": new_target})
    )

    if updated == 0:
        return jsonify({"ok": False, "message": f"Product '{name}' not found in config"})

    with open(cfg_path, "w") as f:
        yaml_parser.dump(cfg, f)

    return jsonify({"ok": True, "message": f"Saved — updated {updated} entr{'y' if updated == 1 else 'ies'}"})


if __name__ == "__main__":
    # Regenerate dashboard on startup so any config or port changes are reflected
    _config = price_tracker.load_config()
    _history = price_tracker.load_history()
    price_tracker.generate_dashboard(_config, _history)
    print("Dashboard: http://127.0.0.1:8080")
    app.run(host="127.0.0.1", port=8080, debug=False)
