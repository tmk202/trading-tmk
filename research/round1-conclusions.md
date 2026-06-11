# Trading Research — Round 1

## Status: CLOSED

## Hypothesis Rejected

**Indicator-based breakout/trend systems using OHLCV data alone do not show durable edge across tested assets and timeframes.**

## Tested

| Test | Result |
|------|--------|
| BTC 5m breakout | PF ≈ 1 |
| BTC 4H breakout | PF ≈ 1 |
| GLD 1H breakout | PF ≈ 1 |
| GLD 1D breakout | PF ≈ 1 |
| Volume filters | ❌ No improvement |
| ADX filters | ❌ No improvement |
| Retest entries | ❌ No improvement |
| Regime classifiers | ❌ No improvement |
| Long-only | ❌ |
| Short mirror | ❌ No edge in crypto downtrend |
| ATR exits | ❌ All 3 multipliers fail |
| Funding flip (H3) | ⏳ Insufficient data |
| OI+Funding (H9) | ⏳ Insufficient data |

## Conclusion

All variants converge toward PF ≈ 1. No statistically significant edge found.

The issue is not:
- Wrong indicator
- Wrong timeframe
- Wrong asset

The issue is: **OHLCV + Indicator framework extracts information that markets have already priced in.**

## Open Questions

1. Does **market microstructure** (funding, OI, liquidation) contain exploitable edge?
2. Does **alternative data** (sentiment, ETF flows, whale wallets) contain exploitable edge?
3. Are **non-directional strategies** (market making, arbitrage, basis trading) superior?

## Next Round

- Funding Rate
- Open Interest
- Liquidations
- Sentiment
- ETF Flows
- Basis Trades
- Market Making

## Data Being Collected

Background crawler running: `collector.py`
- `data/funding_rate.csv` — since June 2026
- `data/open_interest.csv` — since June 2026
