from __future__ import annotations

import csv
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from copy_trade.executor import CopyTradeExecutor, TradeSignal, _to_float

logger = logging.getLogger(__name__)

FOREX_SYMBOLS: dict[str, str] = {
    "EURUSD": "EUR/USD",
    "EUR/USD": "EUR/USD",
    "GBPUSD": "GBP/USD",
    "GBP/USD": "GBP/USD",
    "USDJPY": "USD/JPY",
    "USD/JPY": "USD/JPY",
    "USDCHF": "USD/CHF",
    "USD/CHF": "USD/CHF",
    "AUDUSD": "AUD/USD",
    "AUD/USD": "AUD/USD",
    "USDCAD": "USD/CAD",
    "USD/CAD": "USD/CAD",
    "NZDUSD": "NZD/USD",
    "NZD/USD": "NZD/USD",
    "EURGBP": "EUR/GBP",
    "EUR/GBP": "EUR/GBP",
    "EURJPY": "EUR/JPY",
    "EUR/JPY": "EUR/JPY",
    "GBPJPY": "GBP/JPY",
    "GBP/JPY": "GBP/JPY",
    "EURCHF": "EUR/CHF",
    "EUR/CHF": "EUR/CHF",
    "AUDJPY": "AUD/JPY",
    "AUD/JPY": "AUD/JPY",
    "XAUUSD": "XAU/USD",
    "XAU/USD": "XAU/USD",
    "XAGUSD": "XAG/USD",
    "XAG/USD": "XAG/USD",
}

FOREX_POSITION_SIZE_DEFAULT: dict[str, float] = {
    "XAU/USD": 1000,
    "XAG/USD": 5000,
}


def _normalize_forex_symbol(raw: str) -> str:
    return FOREX_SYMBOLS.get(raw.upper().replace(" ", ""), raw.upper())


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class CTraderExchange:
    """Adapter for cTrader Open API (OAuth2 Client Credentials)."""

    def __init__(self):
        from config import Config

        self.client_id = Config.CTRADER_CLIENT_ID
        self.client_secret = Config.CTRADER_CLIENT_SECRET
        self.account_id = Config.CTRADER_ACCOUNT_ID
        self.demo = Config.CTRADER_DEMO
        self.api_base = (
            "https://demo.ctraderapi.com"
            if self.demo
            else "https://openapi.ctrader.com"
        )
        self.token: str | None = None
        self.token_expiry: float = 0.0
        self.session = requests.Session()
        self._symbol_map: dict[str, int] = {}
        self._symbol_name_map: dict[int, str] = {}
        self._connected = False

    def connect(self) -> bool:
        if self._connected:
            return True
        if not self.client_id or not self.client_secret:
            logger.warning("cTrader: missing CTRADER_CLIENT_ID or CTRADER_CLIENT_SECRET")
            return False
        try:
            self._refresh_token()
            self._load_symbols()
            self._connected = True
            logger.info(
                "cTrader connected (demo=%s, account=%s)",
                self.demo,
                self.account_id or "?",
            )
            return True
        except Exception as exc:
            logger.error("cTrader connection failed: %s", exc)
            return False

    def _refresh_token(self) -> None:
        now = time.time()
        if self.token and now < self.token_expiry - 60:
            return
        url = f"{self.api_base}/apps/token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "accounts+trading",
        }
        resp = self.session.post(url, data=payload, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        self.token = data["access_token"]
        self.token_expiry = now + data.get("expires_in", 3600)
        self.session.headers.update(
            {"Authorization": f"Bearer {self.token}"}
        )

    def _request(self, method: str, path: str, **kwargs) -> Any:
        self._refresh_token()
        url = f"{self.api_base}{path}"
        resp = self.session.request(method, url, timeout=30, **kwargs)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and data.get("errorCode"):
            raise RuntimeError(
                f"cTrader API error {data.get('errorCode')}: {data.get('description', data)}"
            )
        return data

    def _load_symbols(self) -> None:
        if not self.account_id:
            accounts = self.list_accounts()
            if not accounts:
                raise RuntimeError("No cTrader accounts found")
            self.account_id = str(accounts[0].get("accountId", ""))
        data = self._request(
            "GET", f"/v2/accounts/{self.account_id}/symbols"
        )
        for sym in data.get("symbols", []):
            name = str(sym.get("symbolName", "")).upper()
            sid = sym.get("symbolId")
            if name and sid:
                normalized = _normalize_forex_symbol(name)
                self._symbol_map[normalized] = sid
                self._symbol_map[name] = sid
                self._symbol_name_map[sid] = name

    def list_accounts(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/v1/accounts")
        return data.get("accounts", [])

    def get_symbol_id(self, symbol: str) -> int | None:
        sym = _normalize_forex_symbol(symbol)
        return self._symbol_map.get(sym)

    def get_ticker(self, symbol: str) -> dict:
        sid = self.get_symbol_id(symbol)
        if not sid:
            raise ValueError(f"Symbol {symbol} not found on cTrader")
        data = self._request(
            "GET",
            f"/v2/accounts/{self.account_id}/symbols/{sid}",
        )
        quote = data.get("quote", {})
        ask = quote.get("ask")
        bid = quote.get("bid")
        if ask and bid:
            return {"last": (ask + bid) / 2, "ask": ask, "bid": bid}
        return {"last": 0, "ask": 0, "bid": 0}

    def get_balance(self) -> dict[str, float]:
        data = self._request(
            "GET", f"/v2/accounts/{self.account_id}"
        )
        acc = data.get("account", data)
        return {
            "balance": float(acc.get("balance", 0)),
            "equity": float(acc.get("equity", 0)),
            "margin": float(acc.get("usedMargin", 0)),
            "free_margin": float(acc.get("freeMargin", 0)),
        }

    def create_market_order(
        self,
        symbol: str,
        side: str,
        volume: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        comment: str = "copy-trade-bot",
    ) -> dict:
        sid = self.get_symbol_id(symbol)
        if not sid:
            raise ValueError(f"Symbol {symbol} not found on cTrader")
        trade_side = "BUY" if side.lower() in ("buy", "long") else "SELL"
        payload: dict[str, Any] = {
            "symbolId": sid,
            "tradeSide": trade_side,
            "volume": round(volume),
            "orderType": "MARKET",
            "comment": comment,
        }
        if stop_loss is not None:
            payload["stopLoss"] = round(stop_loss, 5)
        if take_profit is not None:
            payload["takeProfit"] = round(take_profit, 5)

        logger.info(
            "cTrader MARKET %s %s vol=%.0f sl=%s tp=%s",
            trade_side,
            symbol,
            volume,
            stop_loss,
            take_profit,
        )
        return self._request(
            "POST",
            f"/v2/accounts/{self.account_id}/orders",
            json=payload,
        )

    def fetch_positions(self) -> list[dict[str, Any]]:
        data = self._request(
            "GET", f"/v2/accounts/{self.account_id}/positions"
        )
        return data.get("positions", [])

    def close_position(self, position_id: str) -> dict:
        return self._request(
            "DELETE",
            f"/v2/accounts/{self.account_id}/positions/{position_id}",
        )


class ForexCopyExecutor(CopyTradeExecutor):
    def __init__(
        self,
        data_dir: str,
        dry_run: bool = True,
        interval: float = 60,
        max_positions: int = 3,
        position_size_usd: float = 1000,
        min_confidence: float = 0.60,
        stop_loss_pct: float = 5.0,
        take_profit_pct: float = 10.0,
        max_daily_loss_pct: float = 10.0,
        max_consecutive_losses: int = 3,
        total_capital: float = 5000.0,
        traders_csv: str = "forex_traders.csv",
        positions_csv: str = "forex_positions.csv",
    ):
        super().__init__(
            data_dir, dry_run, interval,
            stop_loss_pct, take_profit_pct,
            max_daily_loss_pct, max_consecutive_losses, total_capital,
        )
        self.max_positions = max_positions
        self.position_size_usd = position_size_usd
        self.min_confidence = min_confidence
        self.traders_csv = traders_csv
        self.positions_csv = positions_csv
        self.exchange: CTraderExchange | None = None
        self._processed_signals: set[str] = set()

    def _init_exchange(self) -> None:
        if self.exchange is not None:
            return
        if self.dry_run:
            return
        try:
            self.exchange = CTraderExchange()
            if self.exchange.connect():
                balance = self.exchange.get_balance()
                logger.info(
                    "cTrader balance: equity=%.2f, free_margin=%.2f",
                    balance["equity"],
                    balance["free_margin"],
                )
        except Exception as exc:
            logger.warning("cTrader init failed (will simulate dry-run): %s", exc)

    def collect_signals(self) -> list[TradeSignal]:
        return self._collect_from_csv()

    def _collect_from_csv(self) -> list[TradeSignal]:
        positions = self._load_csv(self.positions_csv)
        if not positions:
            logger.debug("No forex positions data (need %s)", self.positions_csv)
            return []

        traders = {}
        traders_path = os.path.join(self.data_dir, self.traders_csv)
        if os.path.exists(traders_path):
            for row in self._load_csv(self.traders_csv):
                tid = row.get("trader_id") or row.get("wallet") or ""
                if tid:
                    traders[tid] = row

        signals: list[TradeSignal] = []
        pair_buckets: dict[str, dict[str, Any]] = {}

        for row in positions:
            symbol = _normalize_forex_symbol(
                row.get("symbol") or row.get("pair") or ""
            )
            if not symbol:
                continue
            side = (row.get("side") or row.get("direction") or "").lower()
            if side not in ("buy", "sell", "long", "short"):
                continue
            side = "buy" if side in ("buy", "long") else "sell"
            trader_id = row.get("trader_id") or row.get("wallet") or ""
            entry_price = _to_float(row.get("entry_price"))
            size = _to_float(row.get("size") or row.get("volume"))

            bucket = pair_buckets.setdefault(symbol, {
                "symbol": symbol,
                "buy_count": 0,
                "sell_count": 0,
                "traders": set(),
                "total_weight": 0.0,
                "weighted_entry": 0.0,
                "total_size": 0.0,
            })
            trader = traders.get(trader_id, {})
            win_rate = _to_float(trader.get("win_rate") or trader.get("win_rate_pct")) or 50
            pnl = _to_float(trader.get("pnl_30d") or trader.get("pnl")) or 0
            weight = max(0.5, min(2.0, (win_rate / 50) + (min(pnl / 5000, 1))))

            if side == "buy":
                bucket["buy_count"] += 1
            else:
                bucket["sell_count"] += 1
            bucket["traders"].add(trader_id)
            bucket["total_weight"] += weight
            if entry_price:
                bucket["weighted_entry"] += entry_price * weight
            if size:
                bucket["total_size"] += size * weight

        for symbol, bucket in pair_buckets.items():
            total = bucket["buy_count"] + bucket["sell_count"]
            if total < 1:
                continue
            if bucket["buy_count"] >= bucket["sell_count"]:
                side = "buy"
                direction_count = bucket["buy_count"]
            else:
                side = "sell"
                direction_count = bucket["sell_count"]

            confidence = direction_count / total * 0.9
            win_rate_bonus = min(len(bucket["traders"]) / 10, 0.1)
            confidence = min(confidence + win_rate_bonus, 0.95)

            if confidence < self.min_confidence:
                continue

            avg_entry = (
                bucket["weighted_entry"] / bucket["total_weight"]
                if bucket["total_weight"] > 0
                else None
            )

            sig_id = f"{symbol}:{side}:{_now_iso()[:16]}"
            if sig_id in self._processed_signals:
                continue
            self._processed_signals.add(sig_id)

            signals.append(TradeSignal(
                symbol=symbol,
                side=side,
                confidence=confidence,
                source="forex_csv",
                source_symbol=symbol,
                trader_count=len(bucket["traders"]),
                entry_price=avg_entry,
                size_usd=self.position_size_usd,
            ))

        signals.sort(key=lambda s: s.trader_count, reverse=True)
        if signals:
            logger.info(
                "Forex signals: %d pairs, top=%s (%d traders, %.0f%% conf)",
                len(signals),
                signals[0].symbol,
                signals[0].trader_count,
                signals[0].confidence * 100,
            )
        return signals[:self.max_positions]

    def execute_signal(self, signal: TradeSignal) -> bool:
        self._init_exchange()
        active_count = len(self.active_positions)
        if active_count >= self.max_positions:
            logger.info(
                "Max forex positions reached (%d), skip %s %s",
                self.max_positions, signal.side, signal.symbol,
            )
            return False

        if signal.symbol in self.active_positions:
            logger.debug("Already have position for %s", signal.symbol)
            return False

        size_usd = self.position_size_usd
        pair = signal.symbol
        for key, default_size in FOREX_POSITION_SIZE_DEFAULT.items():
            if pair.upper() == key.upper():
                size_usd = default_size
                break

        if self.dry_run:
            logger.info(
                "[DRY-RUN] FOREX %s %s $%.0f (conf=%.0f%%, traders=%d)",
                signal.side.upper(), signal.symbol, size_usd,
                signal.confidence * 100, signal.trader_count,
            )
            self.active_positions[signal.symbol] = {
                "side": signal.side,
                "entry_time": _now_iso(),
                "signal": signal,
                "size_usd": size_usd,
            }
            self._log_trade(
                "open", signal.symbol, signal.side, signal.entry_price,
                size_usd, signal.source, signal.source_symbol,
                signal.trader_count, dry_run=True,
            )
            return True

        if self.exchange is None:
            logger.warning("cTrader not connected, skip live execution")
            return False

        try:
            balance = self.exchange.get_balance()
            if balance["free_margin"] < 100:
                logger.warning(
                    "Insufficient margin: free=%.2f", balance["free_margin"]
                )
                return False

            ticker = self.exchange.get_ticker(signal.symbol)
            price = ticker["last"]
            if not price:
                logger.warning("No price for %s", signal.symbol)
                return False

            volume = self._calc_forex_volume(signal.symbol, size_usd, price)

            order = self.exchange.create_market_order(
                symbol=signal.symbol,
                side=signal.side,
                volume=volume,
            )

            self.active_positions[signal.symbol] = {
                "side": signal.side,
                "entry_price": price,
                "entry_time": _now_iso(),
                "volume": volume,
                "order": order,
                "signal": signal,
                "size_usd": size_usd,
            }
            logger.info(
                "FOREX EXECUTED %s %s vol=%.0f @ %.5f",
                signal.side.upper(), signal.symbol, volume, price,
            )
            self._log_trade(
                "open", signal.symbol, signal.side, price, size_usd,
                signal.source, signal.source_symbol, signal.trader_count,
                tx_id=str(order.get("orderId", "")), dry_run=False,
            )
            return True
        except Exception as exc:
            logger.error("Forex execute failed %s %s: %s", signal.side, signal.symbol, exc)
            return False

    def _calc_forex_volume(self, symbol: str, size_usd: float, price: float) -> float:
        return size_usd / price

    def _get_current_price(self, symbol: str) -> float:
        if self.exchange is None:
            self._init_exchange()
        if self.exchange is None:
            try:
                import ccxt
                pub = ccxt.forex()
                ticker = pub.fetch_ticker(symbol)
                return float(ticker["last"])
            except Exception:
                return 0.0
        try:
            ticker = self.exchange.get_ticker(symbol)
            return ticker["last"]
        except Exception:
            return 0.0

    def _sync_positions(self) -> None:
        if self.dry_run or self.exchange is None:
            return
        try:
            positions = self.exchange.fetch_positions()
            for pos in positions:
                sid = pos.get("symbolId")
                if not sid:
                    continue
                symbol = self.exchange._symbol_name_map.get(int(sid), "")
                if not symbol:
                    continue
                symbol = _normalize_forex_symbol(symbol)
                if symbol in self.active_positions:
                    continue
                trade_side = str(pos.get("tradeSide", "")).upper()
                side = "buy" if trade_side == "BUY" else "sell"
                self.active_positions[symbol] = {
                    "side": side,
                    "entry_price": float(pos.get("openPrice", 0)),
                    "entry_time": _now_iso(),
                    "position_id": pos.get("positionId"),
                    "volume": float(pos.get("volume", 0)),
                }
                logger.info("Synced position from cTrader: %s %s", side, symbol)
        except Exception as exc:
            logger.warning("Position sync failed: %s", exc)

    def close_position(self, symbol: str) -> bool:
        pos = self.active_positions.get(symbol)
        if not pos:
            return False

        pnl_usd = self._calc_pnl(symbol, pos)

        if self.dry_run:
            logger.info(
                "[DRY-RUN] CLOSE FOREX %s %s pnl=%+.2f",
                pos["side"], symbol, pnl_usd,
            )
            self._log_trade(
                "close_dry", symbol, pos["side"], None, 0,
                "forex", symbol, pnl=pnl_usd, dry_run=True,
            )
            self.active_positions.pop(symbol, None)
            self._daily_pnl += pnl_usd
            return True

        if self.exchange is None:
            return False

        try:
            position_id = pos.get("position_id")
            if not position_id:
                positions = self.exchange.fetch_positions()
                for p in positions:
                    sid = p.get("symbolId")
                    if not sid:
                        continue
                    sname = _normalize_forex_symbol(
                        self.exchange._symbol_name_map.get(int(sid), "")
                    )
                    if sname == symbol:
                        position_id = str(p.get("positionId", ""))
                        break

            if position_id:
                self.exchange.close_position(position_id)
            else:
                logger.warning("No position_id for %s, cannot close", symbol)

            self.active_positions.pop(symbol, None)
            self._daily_pnl += pnl_usd
            logger.info("CLOSED FOREX %s pnl=%+.2f", symbol, pnl_usd)
            self._log_trade(
                "close", symbol, pos["side"], None, 0,
                "forex", symbol, pnl=pnl_usd, dry_run=False,
            )
            return True
        except Exception as exc:
            logger.error("Forex close failed %s: %s", symbol, exc)
            return False


def build_forex_executor(
    data_dir: str,
    dry_run: bool = True,
    interval: float = 60,
    max_positions: int = 3,
    position_size_usd: float = 1000,
    min_confidence: float = 0.60,
    stop_loss_pct: float = 5.0,
    take_profit_pct: float = 10.0,
    max_daily_loss_pct: float = 10.0,
    max_consecutive_losses: int = 3,
    total_capital: float = 5000.0,
    traders_csv: str = "forex_traders.csv",
    positions_csv: str = "forex_positions.csv",
) -> ForexCopyExecutor:
    return ForexCopyExecutor(
        data_dir=data_dir,
        dry_run=dry_run,
        interval=interval,
        max_positions=max_positions,
        position_size_usd=position_size_usd,
        min_confidence=min_confidence,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        max_daily_loss_pct=max_daily_loss_pct,
        max_consecutive_losses=max_consecutive_losses,
        total_capital=total_capital,
        traders_csv=traders_csv,
        positions_csv=positions_csv,
    )
