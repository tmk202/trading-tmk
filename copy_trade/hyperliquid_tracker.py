from __future__ import annotations

import csv
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests

INFO_URL = "https://api.hyperliquid.xyz/info"
DEXLY_LEADERBOARD_URL = "https://dexly.trade/hyperliquid/leaderboard"


@dataclass
class HyperliquidPositionEvent:
    collected_at: str
    wallet: str
    event_type: str
    coin: str
    side: str
    previous_size: float
    current_size: float
    size_delta: float
    entry_price: float | None
    position_value: float | None
    unrealized_pnl: float | None
    return_on_equity: float | None
    liquidation_price: float | None
    leverage: float | None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        data = self.__dict__.copy()
        data["raw"] = json.dumps(self.raw or {}, ensure_ascii=False, sort_keys=True)
        return data


@dataclass
class HyperliquidFillEvent:
    collected_at: str
    wallet: str
    fill_time: str
    coin: str
    direction: str
    side: str
    price: float | None
    size: float | None
    start_position: float | None
    closed_pnl: float | None
    fee: float | None
    fee_token: str
    hash: str
    oid: str
    tid: str
    raw: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        data = self.__dict__.copy()
        data["raw"] = json.dumps(self.raw or {}, ensure_ascii=False, sort_keys=True)
        return data


class HyperliquidTracker:
    def __init__(self, timeout: int = 20, sleep_s: float = 0.1, retries: int = 3):
        self.timeout = timeout
        self.sleep_s = sleep_s
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
        })

    def fetch_leaderboard_wallets(
        self,
        url: str = DEXLY_LEADERBOARD_URL,
        limit: int = 25,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        html = resp.text
        normalized = html.replace('\\"', '"')
        rows_by_addr: dict[str, dict[str, Any]] = {}

        for match in re.finditer(r'"ethAddress":"(0x[a-fA-F0-9]{40})"', normalized):
            addr = match.group(1).lower()
            window = normalized[match.start(): match.start() + 900]
            rows_by_addr.setdefault(addr, {
                "wallet": addr,
                "source": url,
                "display_name": _extract_json_scalar(window, "displayName"),
                "account_value": _extract_json_number(window, "accountValue"),
                "active_24h": _extract_json_bool(window, "active24h"),
                "is_hft": _extract_json_bool(window, "isHft"),
                "fills_per_min": _extract_json_number(window, "fillsPerMin"),
                "snapshot_rank": _extract_json_number(window, "snapshotRank"),
                "pnl": _extract_json_number(window, "pnl"),
                "roi": _extract_json_number(window, "roi"),
                "volume": _extract_json_number(window, "vlm"),
            })

        if not rows_by_addr:
            for addr in re.findall(r"/hyperliquid/leaderboard/(0x[a-fA-F0-9]{40})", normalized):
                rows_by_addr.setdefault(addr.lower(), {"wallet": addr.lower(), "source": url})

        rows = list(rows_by_addr.values())
        if active_only:
            rows = [row for row in rows if row.get("active_24h") is True]
        return rows[:limit]

    def clearinghouse_state(self, wallet: str) -> dict[str, Any]:
        data = self._post({"type": "clearinghouseState", "user": wallet})
        return data if isinstance(data, dict) else {}

    def user_fills(self, wallet: str) -> list[dict[str, Any]]:
        data = self._post({"type": "userFills", "user": wallet})
        return data if isinstance(data, list) else []

    def user_fills_by_time(
        self,
        wallet: str,
        start_time_ms: int,
        end_time_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"type": "userFillsByTime", "user": wallet, "startTime": start_time_ms}
        if end_time_ms is not None:
            payload["endTime"] = end_time_ms
        data = self._post(payload)
        return data if isinstance(data, list) else []

    def collect_wallet(
        self,
        wallet: str,
        state: dict[str, Any],
        emit_initial_positions: bool = False,
        fill_limit: int = 50,
    ) -> tuple[list[HyperliquidPositionEvent], list[HyperliquidFillEvent], dict[str, Any]]:
        wallet_state = state.setdefault(wallet, {})
        old_positions = wallet_state.get("positions") or {}
        old_seen_fills = set(wallet_state.get("seen_fills") or [])

        ch_state = self.clearinghouse_state(wallet)
        current_positions = _position_map(ch_state)
        position_events = diff_positions(
            wallet=wallet,
            previous=old_positions,
            current=current_positions,
            emit_initial=emit_initial_positions and not old_positions,
        )

        last_fill_time_ms = _to_int(wallet_state.get("last_fill_time_ms"))
        if last_fill_time_ms:
            fills = self.user_fills_by_time(wallet, last_fill_time_ms + 1)
        else:
            fills = self.user_fills(wallet)[:fill_limit]
        fill_events = []
        newest_fill_ids = []
        max_fill_time_ms = last_fill_time_ms or 0
        for fill in fills:
            fill_id = _fill_id(fill)
            if not fill_id:
                continue
            newest_fill_ids.append(fill_id)
            fill_time_ms = _to_int(fill.get("time")) or 0
            max_fill_time_ms = max(max_fill_time_ms, fill_time_ms)
            if fill_id in old_seen_fills:
                continue
            fill_events.append(_fill_event(wallet, fill))

        wallet_state["positions"] = current_positions
        wallet_state["seen_fills"] = list(dict.fromkeys(newest_fill_ids + list(old_seen_fills)))[:5000]
        wallet_state["last_fill_time_ms"] = max_fill_time_ms
        wallet_state["last_account_value"] = _to_float((ch_state.get("marginSummary") or {}).get("accountValue"))
        wallet_state["last_total_ntl_pos"] = _to_float((ch_state.get("marginSummary") or {}).get("totalNtlPos"))
        wallet_state["updated_at"] = _now_iso()
        return position_events, fill_events, state

    def _post(self, payload: dict[str, Any]) -> Any:
        last_error = None
        for attempt in range(self.retries):
            try:
                resp = self.session.post(INFO_URL, json=payload, timeout=self.timeout)
                if resp.status_code == 429:
                    last_error = f"HTTP 429 rate limited for {payload.get('type')}"
                    time.sleep(1 + attempt)
                    continue
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                last_error = exc
                if attempt < self.retries - 1:
                    time.sleep(1 + attempt)
        raise RuntimeError(f"Hyperliquid API failed for {payload.get('type')}: {last_error}")


def diff_positions(
    wallet: str,
    previous: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
    emit_initial: bool = False,
) -> list[HyperliquidPositionEvent]:
    events = []
    now = _now_iso()
    for coin in sorted(set(previous) | set(current)):
        prev = previous.get(coin) or {}
        cur = current.get(coin) or {}
        prev_size = _to_float(prev.get("szi")) or 0.0
        cur_size = _to_float(cur.get("szi")) or 0.0
        if not emit_initial and abs(prev_size - cur_size) < 1e-12:
            continue
        if emit_initial and abs(cur_size) < 1e-12:
            continue
        event_type = _position_event_type(prev_size, cur_size)
        if event_type == "unchanged":
            continue
        side = "long" if cur_size > 0 else "short" if cur_size < 0 else "flat"
        events.append(HyperliquidPositionEvent(
            collected_at=now,
            wallet=wallet,
            event_type=event_type,
            coin=coin,
            side=side,
            previous_size=prev_size,
            current_size=cur_size,
            size_delta=cur_size - prev_size,
            entry_price=_to_float(cur.get("entryPx")),
            position_value=_to_float(cur.get("positionValue")),
            unrealized_pnl=_to_float(cur.get("unrealizedPnl")),
            return_on_equity=_to_float(cur.get("returnOnEquity")),
            liquidation_price=_to_float(cur.get("liquidationPx")),
            leverage=_leverage(cur),
            raw={"previous": prev, "current": cur},
        ))
    return events


def load_hyperliquid_state(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def save_hyperliquid_state(path: str, state: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)


def load_hyperliquid_wallets(path: str, limit: int = 25) -> list[str]:
    wallets = []
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            wallet = row.get("wallet") or row.get("address") or row.get("trader_id") or row.get("ethAddress")
            if wallet and wallet.startswith("0x") and wallet not in wallets:
                wallets.append(wallet.lower())
            if len(wallets) >= limit:
                break
    return wallets


def _position_map(ch_state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out = {}
    for item in ch_state.get("assetPositions") or []:
        pos = item.get("position") or {}
        coin = pos.get("coin")
        size = _to_float(pos.get("szi")) or 0.0
        if coin and abs(size) > 1e-12:
            out[str(coin)] = pos
    return out


def _position_event_type(previous_size: float, current_size: float) -> str:
    if abs(previous_size) < 1e-12 and abs(current_size) >= 1e-12:
        return "open"
    if abs(previous_size) >= 1e-12 and abs(current_size) < 1e-12:
        return "close"
    if previous_size * current_size < 0:
        return "flip"
    if abs(current_size) > abs(previous_size):
        return "increase"
    if abs(current_size) < abs(previous_size):
        return "reduce"
    return "unchanged"


def _fill_event(wallet: str, fill: dict[str, Any]) -> HyperliquidFillEvent:
    direction = str(fill.get("dir") or "")
    side = "buy" if fill.get("side") == "B" else "sell" if fill.get("side") == "A" else ""
    return HyperliquidFillEvent(
        collected_at=_now_iso(),
        wallet=wallet,
        fill_time=_ms_iso(fill.get("time")),
        coin=str(fill.get("coin") or ""),
        direction=direction,
        side=side,
        price=_to_float(fill.get("px")),
        size=_to_float(fill.get("sz")),
        start_position=_to_float(fill.get("startPosition")),
        closed_pnl=_to_float(fill.get("closedPnl")),
        fee=_to_float(fill.get("fee")),
        fee_token=str(fill.get("feeToken") or ""),
        hash=str(fill.get("hash") or ""),
        oid=str(fill.get("oid") or ""),
        tid=str(fill.get("tid") or ""),
        raw=fill,
    )


def _fill_id(fill: dict[str, Any]) -> str:
    tid = fill.get("tid")
    if tid not in (None, ""):
        return str(tid)
    return f"{fill.get('hash')}:{fill.get('oid')}:{fill.get('coin')}:{fill.get('time')}:{fill.get('sz')}"


def _leverage(pos: dict[str, Any]) -> float | None:
    lev = pos.get("leverage")
    if isinstance(lev, dict):
        return _to_float(lev.get("value"))
    return _to_float(lev)


def _extract_json_scalar(text: str, key: str) -> str:
    match = re.search(rf'"{re.escape(key)}":(?:"([^"]*)"|null)', text)
    return match.group(1) if match and match.group(1) is not None else ""


def _extract_json_number(text: str, key: str) -> float | None:
    match = re.search(rf'"{re.escape(key)}":(-?\d+(?:\.\d+)?)', text)
    return _to_float(match.group(1)) if match else None


def _extract_json_bool(text: str, key: str) -> bool | None:
    match = re.search(rf'"{re.escape(key)}":(true|false)', text)
    if not match:
        return None
    return match.group(1) == "true"


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except ValueError:
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ms_iso(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value) / 1000, timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return ""
