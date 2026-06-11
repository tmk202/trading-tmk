from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests

SOL_MINT = "So11111111111111111111111111111111111111112"
STABLE_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "Es9vMFrzaCERmJfrF4H2FYD4E9Gkrb2Z3M8G7WQKfE8": "USDT",
}


@dataclass
class WalletTradeEvent:
    collected_at: str
    block_time: str
    wallet: str
    signature: str
    slot: int | None
    action: str
    token_mint: str
    token_symbol: str
    token_delta: float
    quote_mint: str
    quote_symbol: str
    quote_delta: float
    price_estimate: float | None = None
    fee_sol: float | None = None
    success: bool = True
    raw: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        data = self.__dict__.copy()
        data["raw"] = json.dumps(self.raw or {}, ensure_ascii=False, sort_keys=True)
        return data


class SolanaRpcTracker:
    def __init__(
        self,
        rpc_url: str = "https://solana-rpc.publicnode.com",
        timeout: int = 25,
        sleep_s: float = 0.15,
        retries: int = 3,
    ):
        self.rpc_url = rpc_url
        self.timeout = timeout
        self.sleep_s = sleep_s
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})

    def get_signatures(self, wallet: str, limit: int = 10, until: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if until:
            params["until"] = until
        result = self._rpc("getSignaturesForAddress", [wallet, params])
        if not isinstance(result, list):
            return []
        return result

    def get_transaction(self, signature: str) -> dict[str, Any] | None:
        result = self._rpc("getTransaction", [
            signature,
            {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
        ])
        return result if isinstance(result, dict) else None

    def collect_wallet(
        self,
        wallet: str,
        limit: int = 10,
        seen_signatures: set[str] | None = None,
        include_failed: bool = False,
    ) -> tuple[list[WalletTradeEvent], list[str]]:
        seen_signatures = seen_signatures or set()
        signatures = self.get_signatures(wallet, limit=limit)
        newest = []
        events = []
        for item in signatures:
            signature = item.get("signature")
            if not signature:
                continue
            newest.append(signature)
            if signature in seen_signatures:
                continue
            if item.get("err") and not include_failed:
                continue
            tx = self.get_transaction(signature)
            time.sleep(self.sleep_s)
            if not tx:
                continue
            events.extend(parse_trade_events(wallet, signature, tx))
        return events, newest

    def _rpc(self, method: str, params: list[Any]) -> Any:
        payload = {"jsonrpc": "2.0", "id": int(time.time() * 1000), "method": method, "params": params}
        last_error = None
        for attempt in range(self.retries):
            try:
                resp = self.session.post(self.rpc_url, json=payload, timeout=self.timeout)
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("retry-after") or 0)
                    time.sleep(max(retry_after, 1 + attempt))
                    continue
                resp.raise_for_status()
                data = resp.json()
                if data.get("error"):
                    raise RuntimeError(f"Solana RPC {method} error: {data['error']}")
                return data.get("result")
            except Exception as exc:
                last_error = exc
                if attempt < self.retries - 1:
                    time.sleep(1 + attempt)
        raise RuntimeError(f"Solana RPC {method} failed after {self.retries} attempts: {last_error}")


def parse_trade_events(wallet: str, signature: str, tx: dict[str, Any]) -> list[WalletTradeEvent]:
    meta = tx.get("meta") or {}
    if meta.get("err"):
        return []

    deltas = _token_deltas_for_owner(wallet, meta)
    sol_delta = _sol_delta_for_wallet(wallet, tx)
    if abs(sol_delta) > 0:
        deltas[SOL_MINT] = deltas.get(SOL_MINT, 0.0) + sol_delta

    non_quote = {
        mint: delta
        for mint, delta in deltas.items()
        if abs(delta) > 0 and mint not in STABLE_MINTS and mint != SOL_MINT
    }
    quote_candidates = {
        mint: delta
        for mint, delta in deltas.items()
        if abs(delta) > 0 and (mint in STABLE_MINTS or mint == SOL_MINT)
    }
    if not non_quote:
        return []

    quote_mint, quote_delta = _best_quote_delta(quote_candidates)
    block_time = _block_time_iso(tx.get("blockTime"))
    slot = tx.get("slot")
    fee_sol = (meta.get("fee") or 0) / 1_000_000_000

    events = []
    for mint, token_delta in non_quote.items():
        action = "buy" if token_delta > 0 else "sell"
        price = None
        if quote_delta and token_delta:
            price = abs(quote_delta) / abs(token_delta)
        events.append(WalletTradeEvent(
            collected_at=_now_iso(),
            block_time=block_time,
            wallet=wallet,
            signature=signature,
            slot=slot,
            action=action,
            token_mint=mint,
            token_symbol=_symbol_for_mint(mint),
            token_delta=token_delta,
            quote_mint=quote_mint,
            quote_symbol=_symbol_for_mint(quote_mint),
            quote_delta=quote_delta,
            price_estimate=price,
            fee_sol=fee_sol,
            success=True,
            raw={
                "token_deltas": deltas,
                "quote_candidates": quote_candidates,
            },
        ))
    return events


def load_wallets_from_performance(path: str, limit: int, min_win_rate: float, min_trades: int, min_pnl: float) -> list[str]:
    wallets = []
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if _to_float(row.get("win_rate_pct")) < min_win_rate:
                continue
            if _to_float(row.get("tx")) < min_trades:
                continue
            if _to_float(row.get("pnl")) < min_pnl:
                continue
            wallet = row.get("wallet")
            if wallet:
                wallets.append(wallet)
            if len(wallets) >= limit:
                break
    return wallets


def load_state(path: str) -> dict[str, list[str]]:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def save_state(path: str, state: dict[str, list[str]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)


def _token_deltas_for_owner(wallet: str, meta: dict[str, Any]) -> dict[str, float]:
    before = _token_balance_map(wallet, meta.get("preTokenBalances") or [])
    after = _token_balance_map(wallet, meta.get("postTokenBalances") or [])
    out = {}
    for mint in set(before) | set(after):
        out[mint] = after.get(mint, 0.0) - before.get(mint, 0.0)
    return out


def _token_balance_map(wallet: str, balances: list[dict[str, Any]]) -> dict[str, float]:
    out = {}
    for item in balances:
        if item.get("owner") != wallet:
            continue
        mint = item.get("mint")
        amount = _ui_token_amount(item.get("uiTokenAmount") or {})
        if mint:
            out[mint] = out.get(mint, 0.0) + amount
    return out


def _sol_delta_for_wallet(wallet: str, tx: dict[str, Any]) -> float:
    account_keys = (((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or [])
    index = None
    for idx, item in enumerate(account_keys):
        pubkey = item.get("pubkey") if isinstance(item, dict) else item
        if pubkey == wallet:
            index = idx
            break
    if index is None:
        return 0.0
    meta = tx.get("meta") or {}
    pre = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    if index >= len(pre) or index >= len(post):
        return 0.0
    return (post[index] - pre[index]) / 1_000_000_000


def _best_quote_delta(candidates: dict[str, float]) -> tuple[str, float]:
    if not candidates:
        return "", 0.0
    mint = max(candidates, key=lambda item: abs(candidates[item]))
    return mint, candidates[mint]


def _ui_token_amount(data: dict[str, Any]) -> float:
    value = data.get("uiAmount")
    if value is not None:
        return float(value)
    text = data.get("uiAmountString")
    if text not in (None, ""):
        return float(text)
    amount = data.get("amount")
    decimals = data.get("decimals") or 0
    if amount in (None, ""):
        return 0.0
    return float(amount) / (10 ** int(decimals))


def _symbol_for_mint(mint: str) -> str:
    if mint == SOL_MINT:
        return "SOL"
    return STABLE_MINTS.get(mint, mint[:8])


def _block_time_iso(value: int | None) -> str:
    if not value:
        return ""
    return datetime.fromtimestamp(value, timezone.utc).replace(microsecond=0).isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _to_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except ValueError:
        return 0.0
