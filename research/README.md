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
