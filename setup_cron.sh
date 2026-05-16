#!/usr/bin/env bash
# Adds a daily 9 AM cron job for the price tracker.
# Run once: bash setup_cron.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$(which python3)"
TRACKER="$SCRIPT_DIR/price_tracker.py"
LOG="$SCRIPT_DIR/cron.log"

# Verify python3 exists
if [ -z "$PYTHON" ]; then
    echo "Error: python3 not found on PATH"
    exit 1
fi

# Verify dependencies are installed
if ! "$PYTHON" -c "import requests, bs4, yaml" 2>/dev/null; then
    echo "Installing dependencies..."
    "$PYTHON" -m pip install -r "$SCRIPT_DIR/requirements.txt"
fi

CRON_LINE="0 9 * * * $PYTHON $TRACKER >> $LOG 2>&1"

# Add only if not already present
if crontab -l 2>/dev/null | grep -qF "$TRACKER"; then
    echo "Cron job already exists — no changes made."
else
    (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
    echo "Cron job added: runs daily at 9:00 AM"
    echo "  $CRON_LINE"
fi

echo ""
echo "To verify: crontab -l"
echo "To remove: crontab -e  (delete the price_tracker line)"
echo "Cron output will be appended to: $LOG"
