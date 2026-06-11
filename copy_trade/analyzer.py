from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from copy_trade.models import ConsensusSignal, PositionSnapshot, TraderSnapshot, utc_now_iso


def select_traders(
    traders: Iterable[TraderSnapshot],
    limit: int = 10,
    max_drawdown: float | None = None,
    min_win_rate: float | None = None,
    min_copy_days: int | None = None,
) -> list[TraderSnapshot]:
    selected = []
    for trader in traders:
        if max_drawdown is not None and trader.drawdown is not None and trader.drawdown > max_drawdown:
            continue
        if min_win_rate is not None and trader.win_rate is not None and trader.win_rate < min_win_rate:
            continue
        if min_copy_days is not None and trader.copy_trade_days is not None and trader.copy_trade_days < min_copy_days:
            continue
        selected.append(trader)

    return sorted(
        selected,
        key=lambda item: (
            item.roi_30d if item.roi_30d is not None else -10**9,
            item.win_rate if item.win_rate is not None else -10**9,
        ),
        reverse=True,
    )[:limit]


def build_consensus(
    positions: Iterable[PositionSnapshot],
    selected_traders: Iterable[TraderSnapshot] | None = None,
    threshold: float = 0.70,
) -> list[ConsensusSignal]:
    allowed = None
    if selected_traders is not None:
        allowed = {trader.trader_id for trader in selected_traders}

    grouped: dict[str, list[PositionSnapshot]] = defaultdict(list)
    for pos in positions:
        if allowed is not None and pos.trader_id not in allowed:
            continue
        if not pos.symbol or pos.side not in ("long", "short", "buy", "sell"):
            continue
        normalized_side = "long" if pos.side in ("long", "buy") else "short"
        grouped[pos.symbol.upper()].append(PositionSnapshot(
            collected_at=pos.collected_at,
            platform=pos.platform,
            trader_id=pos.trader_id,
            symbol=pos.symbol.upper(),
            side=normalized_side,
            entry_price=pos.entry_price,
            mark_price=pos.mark_price,
            leverage=pos.leverage,
            size=pos.size,
            notional=pos.notional,
            pnl=pos.pnl,
            pnl_pct=pos.pnl_pct,
            raw=pos.raw,
        ))

    out = []
    now = utc_now_iso()
    for symbol, symbol_positions in grouped.items():
        trader_ids = {pos.trader_id for pos in symbol_positions}
        long_positions = [pos for pos in symbol_positions if pos.side == "long"]
        short_positions = [pos for pos in symbol_positions if pos.side == "short"]
        long_count = len({pos.trader_id for pos in long_positions})
        short_count = len({pos.trader_id for pos in short_positions})
        long_weight = _weight(long_positions)
        short_weight = _weight(short_positions)
        total_count = max(1, len(trader_ids))
        long_ratio = long_count / total_count
        short_ratio = short_count / total_count

        signal = "hold"
        if long_ratio >= threshold and long_count > short_count:
            signal = "long"
        elif short_ratio >= threshold and short_count > long_count:
            signal = "short"

        out.append(ConsensusSignal(
            collected_at=now,
            platform=symbol_positions[0].platform,
            symbol=symbol,
            trader_count=len(trader_ids),
            long_count=long_count,
            short_count=short_count,
            long_weight=long_weight,
            short_weight=short_weight,
            long_ratio=long_ratio,
            short_ratio=short_ratio,
            signal=signal,
        ))

    return sorted(out, key=lambda item: max(item.long_ratio, item.short_ratio), reverse=True)


def _weight(positions: list[PositionSnapshot]) -> float:
    weights = []
    for pos in positions:
        if pos.notional is not None:
            weights.append(abs(pos.notional))
        elif pos.size is not None:
            weights.append(abs(pos.size))
        else:
            weights.append(1.0)
    return sum(weights)

