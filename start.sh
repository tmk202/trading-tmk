#!/bin/bash
set -e
mkdir -p data/copy_trade

export PYTHONUNBUFFERED=1

INTERVAL="${CHECK_INTERVAL_MINUTES:-5}"
COLLECT_INTERVAL="${COPY_TRADE_COLLECT_INTERVAL:-300}"
POSITION_SIZE_USD="${COPY_TRADE_POSITION_SIZE_USD:-100}"
MIN_CONFIDENCE="${COPY_TRADE_MIN_CONFIDENCE:-0.65}"
OKX_MAX_WALLETS="${COPY_TRADE_OKX_MAX_WALLETS:-50}"
MAX_POSITIONS="${COPY_TRADE_MAX_POSITIONS:-3}"

echo "Starting dashboard..."
python web_dashboard.py &

echo "Starting copy trade bot (LIVE Futures testnet)..."
echo "Interval=${INTERVAL}m Collect=${COLLECT_INTERVAL}s Size=\$${POSITION_SIZE_USD} Confidence=${MIN_CONFIDENCE} Wallets=${OKX_MAX_WALLETS} MaxPos=${MAX_POSITIONS}"

# Cleanup: keep only last 2000 lines per CSV to prevent OOM
for f in data/copy_trade/trader_daily_stats.csv data/copy_trade/trader_positions.csv; do
  if [ -f "$f" ] && [ "$(wc -l < "$f")" -gt 2200 ]; then
    head -1 "$f" > "$f.tmp" && tail -2000 "$f" >> "$f.tmp" && mv "$f.tmp" "$f"
  fi
done

python main_copy_trade.py \
  --no-dry-run \
  --mode hyperliquid \
  --interval "$INTERVAL" \
  --collect-interval "$COLLECT_INTERVAL" \
  --max-positions "$MAX_POSITIONS" \
  --position-size-usd "$POSITION_SIZE_USD" \
  --min-confidence "$MIN_CONFIDENCE" \
  --okx-max-wallets "$OKX_MAX_WALLETS"
