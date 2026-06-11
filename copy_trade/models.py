from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class TraderSnapshot:
    collected_at: str
    platform: str
    trader_id: str
    nickname: str = ""
    rank: int | None = None
    roi_7d: float | None = None
    roi_30d: float | None = None
    roi_90d: float | None = None
    pnl_30d: float | None = None
    drawdown: float | None = None
    aum: float | None = None
    followers: int | None = None
    win_rate: float | None = None
    total_trades: int | None = None
    copy_trade_days: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["raw"] = _json_default(self.raw)
        return row


@dataclass
class PositionSnapshot:
    collected_at: str
    platform: str
    trader_id: str
    symbol: str
    side: str
    entry_price: float | None = None
    mark_price: float | None = None
    leverage: float | None = None
    size: float | None = None
    notional: float | None = None
    pnl: float | None = None
    pnl_pct: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["raw"] = _json_default(self.raw)
        return row


@dataclass
class ConsensusSignal:
    collected_at: str
    platform: str
    symbol: str
    trader_count: int
    long_count: int
    short_count: int
    long_weight: float
    short_weight: float
    long_ratio: float
    short_ratio: float
    signal: str

    def to_row(self) -> dict[str, Any]:
        return asdict(self)


def _json_default(value: Any) -> str:
    import json

    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)

