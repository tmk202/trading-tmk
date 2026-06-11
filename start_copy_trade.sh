#!/bin/bash
# Copy Trade Bot Launcher
# Usage:
#   ./start_copy_trade.sh              # dry-run daemon
#   ./start_copy_trade.sh --no-dry-run # live trading
#   ./start_copy_trade.sh --once       # run once
#   ./start_copy_trade.sh stop         # stop daemon

DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="$DIR/data/copy_trade/bot.pid"
LOGFILE="$DIR/data/copy_trade/bot.log"

case "${1:-}" in
  stop)
    if [ -f "$PIDFILE" ]; then
      PID="$(cat "$PIDFILE")"
      kill "$PID" 2>/dev/null
      rm "$PIDFILE"
      echo "Bot stopped (PID $PID)"
    else
      echo "No PID file found"
    fi
    exit 0
    ;;
  --once)
    cd "$DIR" && python3 main_copy_trade.py --once "${@:2}"
    exit $?
    ;;
  --no-dry-run)
    EXTRA="--no-dry-run"
    shift
    ;;
  *)
    EXTRA="--dry-run"
    ;;
esac

cd "$DIR"
nohup python3 -u main_copy_trade.py $EXTRA "$@" >> "$LOGFILE" 2>&1 &
PID=$!
echo $PID > "$PIDFILE"
echo "Copy Trade Bot started (PID $PID, dry-run=${EXTRA})"
echo "Log: $LOGFILE"
echo "Stop: $0 stop"
