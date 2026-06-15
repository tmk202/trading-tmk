# Copy Trade Research

Track Money: trader positioning / copy-trade research.

Goal: answer whether top-trader positioning has edge before wiring any live copy bot.

## Status

Implemented:

- Provider-based collector (`bitget`, `binance`, `polymarket`, `hyperliquid`, `okx_web3`, `csv`)
- Browser scraper/discovery via Chrome/CloakBrowser-compatible `--dump-dom`
- CSV/JSONL storage in `data/copy_trade/`
- Trader filtering
- Wallet watchlist scoring
- Position consensus signal
- CLI entry point: `copy_trade_lab.py`

Known source status:

- Binance leaderboard public endpoint is no longer reliably public; old endpoints return 404/private.
- Bitget V1 copy-trade endpoint is decommissioned; V2/public access is not guaranteed and may require auth.
- Polymarket Data API is public/no-key and has leaderboard + positions, but this environment currently gets connection refused to `data-api.polymarket.com`.
- Hyperliquid Info API is public/no-key for wallet positions; it needs a wallet list from browser/manual/API discovery.
- OKX Web3 Solana leaderboard is public/no-key and currently exposes embedded wallet rows in HTML. It is on-chain smart-money data; `topTokens` are stored as token-position proxies, not open futures positions.
- CSV import is the reliable path for manual exports, browser exports, or future scraper output.

## Commands

Try public providers:

```bash
python3 copy_trade_lab.py collect --provider bitget --limit 50
python3 copy_trade_lab.py collect --provider binance --limit 50
python3 copy_trade_lab.py collect --provider polymarket --limit 50 --category CRYPTO --time-period MONTH --order-by PNL
python3 copy_trade_lab.py collect --provider polymarket --limit 20 --with-positions --category CRYPTO
python3 copy_trade_lab.py collect --provider hyperliquid --wallets-csv data/copy_trade/sample_hyperliquid_wallets.csv --with-positions
python3 copy_trade_lab.py collect --provider okx_web3 --limit 20 --with-positions --okx-url "https://web3.okx.com/copy-trade/leaderboard/solana"
```

Collect more OKX Web3 wallets across ranking modes:

```bash
python3 copy_trade_lab.py okx-sweep \
  --rank-by pnl,roi,win_rate,volume,tx \
  --per-rank-limit 100 \
  --max-wallets 300 \
  --with-positions
```

Browser discovery, no login first:

```bash
python3 copy_trade_lab.py browser-discover "https://www.bybit.com/copyTrading/en/leaderboard-master" --slug bybit_leaderboard
python3 copy_trade_lab.py browser-discover "https://www.bybit.com/copyTrading/en/leaderboard-master" --mode requests --slug bybit_requests
python3 copy_trade_lab.py browser-discover "https://www.bitget.com/copy-trading/overview" --slug bitget_copy
```

Use CloakBrowser/DCOM-style browser binary/profile:

```bash
export CLOAK_BROWSER_PATH="/path/to/CloakBrowser"
export CLOAK_USER_DATA_DIR="/path/to/profile"
python3 copy_trade_lab.py browser-discover "https://www.bybit.com/copyTrading/en/leaderboard-master" --headful
```

Best-effort browser trader extraction:

```bash
python3 copy_trade_lab.py browser-collect "https://www.bybit.com/copyTrading/en/leaderboard-master" --platform bybit
python3 copy_trade_lab.py browser-collect "https://www.bybit.com/copyTrading/en/leaderboard-master" --mode requests --platform bybit
```

Artifacts are saved in `data/copy_trade/browser_artifacts/`.

Import snapshots from CSV:

```bash
python3 copy_trade_lab.py collect \
  --provider csv \
  --traders-csv data/copy_trade/manual_traders.csv \
  --positions-csv data/copy_trade/manual_positions.csv
```

Rank filtered traders:

```bash
python3 copy_trade_lab.py report \
  --traders-csv data/copy_trade/trader_daily_stats.csv \
  --top 10 \
  --max-drawdown 30 \
  --min-win-rate 50 \
  --min-copy-days 30
```

Build wallet watchlist:

```bash
python3 copy_trade_lab.py watchlist \
  --top 10 \
  --min-pnl 10000 \
  --min-roi 10 \
  --min-win-rate 20 \
  --min-trades 20
```

Build wallet win/loss performance table:

```bash
python3 copy_trade_lab.py wallet-performance \
  --top 150 \
  --min-trades 20 \
  --min-pnl 1000
```

Run near-realtime OKX Web3 monitor:

```bash
python3 copy_trade_lab.py monitor \
  --limit 20 \
  --interval 30 \
  --min-token-wallets 2 \
  --emit-initial
```

The monitor polls the public OKX Web3 leaderboard, writes trader/token ticks, and appends alerts to `data/copy_trade/realtime_alerts.jsonl`.
This is near-realtime leaderboard monitoring, not transaction-level websocket tracking.

Track real Solana wallet transactions:

```bash
python3 copy_trade_lab.py track-wallets \
  --wallet-limit 10 \
  --tx-limit 8 \
  --iterations 1 \
  --min-win-rate 55 \
  --min-trades 100 \
  --min-pnl 5000

python3 copy_trade_lab.py trade-summary --rows 20
```

`track-wallets` reads `wallet_performance.csv`, fetches recent Solana transactions, parses token/SOL balance deltas, and writes:

- `data/copy_trade/wallet_trade_events.csv`
- `data/copy_trade/wallet_trade_events.jsonl`
- `data/copy_trade/solana_tracker_state.json`
- `data/copy_trade/wallet_trade_summary.csv`

The default RPC is `https://solana-rpc.publicnode.com`. Public RPC endpoints are rate-limited; use `SOLANA_RPC_URL` or `--rpc-url` for a private endpoint when running continuously.

Build consensus signal:

```bash
python3 copy_trade_lab.py consensus \
  --traders-csv data/copy_trade/trader_daily_stats.csv \
  --positions-csv data/copy_trade/trader_positions.csv \
  --top 10 \
  --threshold 0.70
```

## CSV Schema

Trader CSV minimum:

```csv
platform,trader_id,nickname,rank,roi_30d,drawdown,aum,followers,win_rate,total_trades,copy_trade_days
```

Position CSV minimum:

```csv
platform,trader_id,symbol,side,entry_price,mark_price,leverage,size,notional,pnl,pnl_pct
```

`side` accepts `long`, `short`, `buy`, or `sell`.

## Hypotheses

- H1: copy top 10 ROI 30d
- H2: copy top ROI with low drawdown
- H3: consensus signal when 70% of selected traders are same direction
- H4: prefer stable AUM/followers growth over ROI spikes

## Promising Wallet Universe (Tier System)

Tìm ví tiềm năng để copy theo 3 tier, kết hợp OKX Web3 (Solana) + Hyperliquid.

| Tier   | ROI  | Trades (OKX) / Volume (HL) | DD   | WR   | PnL min  | Size mult |
|--------|------|---------------------------|------|------|----------|-----------|
| hot    | ≥150%| ≥50 trades / ≥$100M vol  | ≤30% | ≥50% | ≥$10k    | ×2.0      |
| warm   | ≥80% | ≥30 trades / ≥$50M vol   | ≤40% | ≥40% | ≥$5k     | ×1.0      |
| explore| ≥50% | ≥20 trades / ≥$20M vol   | ≤50% | —    | ≥$1k     | ×0.5      |

Hyperliquid bỏ qua WR/DD checks (data không có sẵn), chỉ dùng PnL + Volume + AUM + active_24h.

### Build thủ công

```bash
python3 tier_wallet_universe.py
# → data/copy_trade/promising_wallets.csv
```

### Auto trong pipeline

`main_copy_trade.py` chạy Step [3.5/5] sau mỗi collect cycle (~120s). Output: `data/copy_trade/promising_wallets.csv` với columns: platform, tier, wallet, nickname, pnl_30d, roi_pct, sample, position_size_usd, size_mult.

## Next

1. Refresh OKX + Hyperliquid data mỗi ~2h.
2. Re-score + re-tier mỗi ~6h (track record drift).
3. Track real-time position changes từ tier wallets (Hyperliquid fills + position events).
4. Tie `position_size_usd` từ tier vào executor — hot wallets copy 2x size.
4. Only then consider paper-trade execution.
