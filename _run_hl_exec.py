import sys, logging
sys.stdout = open(sys.stdout.fileno(), 'w', buffering=1)
logging.basicConfig(level=logging.INFO, format='%(message)s',
                    stream=sys.stdout)

from copy_trade.executor import build_hyperliquid_executor

ex = build_hyperliquid_executor(
    data_dir='data/copy_trade',
    dry_run=True,
    interval=9999,
    max_positions=5,
    position_size_usd=50,
    min_confidence=0.70,
    min_delta_notional=1000,
    recent_seconds=3600,
    trusted_wallets_csv='hyperliquid_leaderboard.csv',
    stop_loss_pct=30.0,
    take_profit_pct=50.0,
    total_capital=10000.0,
)

signals = ex.run_once()
print(f'\n=== SIGNALS: {len(signals)} ===')
for s in signals:
    print(f'{s.side.upper():5s} {s.symbol:<12s} conf={s.confidence:.0%} src={s.source} sz=${s.size_usd:.0f} traders={s.trader_count}')

print(f'\nActive positions: {len(ex.active_positions)}')
for sym, pos in ex.active_positions.items():
    sig = pos['signal']
    print(f'  {sym}: {sig.side} sz=${sig.size_usd:.0f} entry={sig.entry_price}')

# Stats
print(f'\n--- Summary ---')
print(f'Total capital: ${ex.total_capital}')
print(f'Daily PnL: ${ex._daily_pnl:.2f}')
print(f'Consecutive losses: {ex._consecutive_losses}')
print(f'Circuit breaker: {ex._circuit_breaker}')
