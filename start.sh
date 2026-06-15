#!/bin/bash
set -e
mkdir -p data/copy_trade

DRY_RUN="${COPY_TRADE_DRY_RUN:-true}"
MODE="${COPY_TRADE_MODE:-both}"
INTERVAL="${CHECK_INTERVAL_MINUTES:-15}"
COLLECT_INTERVAL="${COPY_TRADE_COLLECT_INTERVAL:-120}"
TRACK_INTERVAL="${COPY_TRADE_TRACK_INTERVAL:-30}"
MAX_POSITIONS="${COPY_TRADE_MAX_POSITIONS:-10}"
POSITION_SIZE_USD="${COPY_TRADE_POSITION_SIZE_USD:-100}"
COPY_SIZE_SOL="${COPY_TRADE_SOL_SIZE_SOL:-0.10}"
MIN_CONFIDENCE="${COPY_TRADE_MIN_CONFIDENCE:-0.55}"
MIN_WIN_RATE="${COPY_TRADE_MIN_WIN_RATE:-20}"
MIN_TRADES="${COPY_TRADE_MIN_TRADES:-20}"
TRACK_WALLET_LIMIT="${COPY_TRADE_TRACK_WALLET_LIMIT:-10}"
TRACK_TX_LIMIT="${COPY_TRADE_TRACK_TX_LIMIT:-12}"
OKX_MAX_WALLETS="${COPY_TRADE_OKX_MAX_WALLETS:-500}"

if [ "$DRY_RUN" = "false" ] || [ "$DRY_RUN" = "0" ]; then
  DRY_RUN_FLAG="--no-dry-run"
else
  DRY_RUN_FLAG="--dry-run"
fi

echo "Starting web dashboard on port 8080..."
python web_dashboard.py &
echo "Starting copy trade bot..."
python main_copy_trade.py \
  "$DRY_RUN_FLAG" \
  --mode "$MODE" \
  --interval "$INTERVAL" \
  --collect-interval "$COLLECT_INTERVAL" \
  --track-interval "$TRACK_INTERVAL" \
  --max-positions "$MAX_POSITIONS" \
  --position-size-usd "$POSITION_SIZE_USD" \
  --copy-size-sol "$COPY_SIZE_SOL" \
  --min-confidence "$MIN_CONFIDENCE" \
  --min-win-rate "$MIN_WIN_RATE" \
  --min-trades "$MIN_TRADES" \
  --track-wallet-limit "$TRACK_WALLET_LIMIT" \
  --track-tx-limit "$TRACK_TX_LIMIT" \
  --okx-max-wallets "$OKX_MAX_WALLETS"
