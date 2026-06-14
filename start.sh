#!/bin/bash
set -e
mkdir -p data/copy_trade
echo "Starting web dashboard on port 8080..."
python web_dashboard.py &
echo "Starting copy trade bot..."
python main_copy_trade.py --dry-run --interval 15
