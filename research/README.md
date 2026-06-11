# Research Round #1 — BTC/USDT Breakout Scalping

## Thời gian
Tháng 6, 2026

## Kết luận
**Indicator-based breakout scalping trên BTC 5m không cho thấy edge sau hàng loạt kiểm định.**

## Hypothesis đã bác bỏ

| # | Hypothesis | Trạng thái | Bằng chứng |
|---|-----------|-----------|-----------|
| 1 | Volume breakout có edge | ❌ | 12 trades, 16.7% win, PF=0.65 |
| 2 | ADX lọc được noise | ❌ | V3: PF=1.53 (April) nhưng May chết, tổng PF ~1 |
| 3 | Retest giảm fake breakout | ❌ | V4: PF=0.74, vẫn chết May |
| 4 | Regime classifier cứu strategy | ❌ | V5: PF=0.56, lọc cả lệnh tốt |
| 5 | Short mirror tạo alpha | ❌ | 58 trades, 36% win, PF=0.67 |
| 6 | ATR trailing fix exit | ❌ | 3 versions (2.0/2.5/3.0), tất cả âm |
| 7 | Swing 4H có edge | ❌ | 28 trades/2 năm, PnL +$5.49 (~hòa vốn) |
| 8 | Funding flip (H3) | ⏳ | Thiếu data, funding không đủ extreme |
| 9 | Funding+OI+sideway (H9) | ⏳ | Thiếu OI history |

## Bài học
1. OHLCV breakout trên BTC 5m không có edge — noise > signal
2. Lên timeframe 4H cũng không cứu được
3. Cần non-OHLCV data (funding, OI, liquidation) cho hypothesis tiếp theo
4. Đừng xây hệ thống trước khi có hypothesis

## Data collected
- `data/funding_rate.csv` — snapshot mỗi giờ (bắt đầu Tháng 6, 2026)
- `data/open_interest.csv` — snapshot mỗi giờ (bắt đầu Tháng 6, 2026)

## Strategies implemented
- V1-V5: 5 biến thể scalper (strategy.py)
- short_engine: Pullback-to-EMA short
- Trend following: EmaCross, MacdCross, SuperTrend, RsiEma, BbRsi

## Signal Engine — Round #2
`signal_engine.py` là máy tín hiệu research-first hiện tại. File này chỉ scan/backtest/export tín hiệu, không đặt lệnh.

Commands:

```bash
python3 signal_engine.py scan --days 45
python3 signal_engine.py backtest --days 45 --hold 24
python3 signal_engine.py export --days 45 --hold 24
```

Signals đang test:

- `h9_crowded_long_short`: funding cao + OI tăng + giá sideway/up yếu → short setup
- `h9_crowded_short_long`: funding thấp/âm + OI tăng + giá sideway/down yếu → long setup
- `funding_flip_short`: funding flip dương sang âm → short setup
- `funding_flip_long`: funding flip âm sang dương → long setup
- `flush_rebound_long`: giá flush + funding depressed + OI giảm → rebound setup
- `global_longs_crowded_short`: global long/short ratio cao + giá yếu → short setup
- `top_longs_crowded_short`: top-trader position ratio cao + funding dương + giá yếu → short setup
- `taker_sell_pressure_short`: taker buy/sell ratio yếu + OI tăng → short setup
- `taker_buy_pressure_long`: taker buy/sell ratio mạnh + OI tăng → long setup

Free sources hiện đang dùng:

- Binance OHLCV public API
- Binance funding rate history
- Binance open interest statistics
- Binance global long/short account ratio
- Binance top-trader long/short account ratio
- Binance top-trader long/short position ratio
- Binance taker buy/sell volume ratio

Lưu ý: các endpoint Binance futures sentiment/OI public chỉ kéo được cửa sổ ngắn khoảng 30 ngày, nên kết quả hiện tại mới là exploratory. Cần để collector tích lũy thêm hoặc thêm nguồn OI/liquidation lịch sử khác trước khi coi là edge.

## Copy Trade Research — Money Track

`copy_trade_lab.py` là module research cho trader positioning/copy-trade. Module này không đặt lệnh; nó thu thập hoặc import snapshot top trader/position, lọc trader và build consensus signal.

Commands:

```bash
python3 copy_trade_lab.py collect --provider bitget --limit 50
python3 copy_trade_lab.py collect --provider binance --limit 50
python3 copy_trade_lab.py collect --provider polymarket --limit 50 --category CRYPTO --time-period MONTH --order-by PNL
python3 copy_trade_lab.py collect --provider polymarket --limit 20 --with-positions --category CRYPTO
python3 copy_trade_lab.py collect --provider hyperliquid --wallets-csv data/copy_trade/sample_hyperliquid_wallets.csv --with-positions
python3 copy_trade_lab.py collect --provider csv --traders-csv data/copy_trade/sample_traders.csv --positions-csv data/copy_trade/sample_positions.csv
python3 copy_trade_lab.py report --traders-csv data/copy_trade/trader_daily_stats.csv --top 10
python3 copy_trade_lab.py consensus --traders-csv data/copy_trade/trader_daily_stats.csv --positions-csv data/copy_trade/trader_positions.csv --top 10 --threshold 0.70
```

Source status:

- Binance leaderboard public endpoint cũ hiện trả 404/private.
- Bitget V1 copy-trade endpoint đã decommissioned; V2/public access chưa ổn định và có thể cần auth.
- Polymarket Data API là public/no-key, có leaderboard + positions; hiện local network tới `data-api.polymarket.com` đang bị connection refused.
- Hyperliquid Info API là public/no-key cho wallet positions; cần danh sách wallet từ browser/manual/API discovery.
- CSV import hiện là đường chắc nhất để nạp manual export/browser scraper/API output vào analyzer.
