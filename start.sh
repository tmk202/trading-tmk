#!/bin/bash
set -e
mkdir -p data/copy_trade

export PYTHONUNBUFFERED=1

INTERVAL="${CHECK_INTERVAL_MINUTES:-5}"
COLLECT_INTERVAL="${COPY_TRADE_COLLECT_INTERVAL:-300}"
POSITION_SIZE_USD="${COPY_TRADE_POSITION_SIZE_USD:-100}"
MIN_CONFIDENCE="${COPY_TRADE_MIN_CONFIDENCE:-0.65}"
OKX_MAX_WALLETS="${COPY_TRADE_OKX_MAX_WALLETS:-100}"
MAX_POSITIONS="${COPY_TRADE_MAX_POSITIONS:-5}"

echo "Starting copy trade bot (LIVE Futures testnet)..."
echo "Interval=${INTERVAL}m Collect=${COLLECT_INTERVAL}s Size=\$${POSITION_SIZE_USD} Confidence=${MIN_CONFIDENCE} Wallets=${OKX_MAX_WALLETS} MaxPos=${MAX_POSITIONS}"

python main_copy_trade.py \
  --no-dry-run \
  --mode hyperliquid \
  --interval "$INTERVAL" \
  --collect-interval "$COLLECT_INTERVAL" \
  --max-positions "$MAX_POSITIONS" \
  --position-size-usd "$POSITION_SIZE_USD" \
  --min-confidence "$MIN_CONFIDENCE" \
  --okx-max-wallets "$OKX_MAX_WALLETS"
