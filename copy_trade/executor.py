from __future__ import annotations

import csv
import json
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

SYMBOL_MAP = {
    "SOL": "SOL/USDT",
    "BTC": "BTC/USDT",
    "ETH": "ETH/USDT",
    "BNB": "BNB/USDT",
    "DOGE": "DOGE/USDT",
    "XRP": "XRP/USDT",
    "AVAX": "AVAX/USDT",
    "LTC": "LTC/USDT",
    "ADA": "ADA/USDT",
    "TON": "TON/USDT",
    "LINK": "LINK/USDT",
    "AAVE": "AAVE/USDT",
    "ARB": "ARB/USDT",
    "OP": "OP/USDT",
    "APT": "APT/USDT",
    "SUI": "SUI/USDT",
    "WLD": "WLD/USDT",
    "TIA": "TIA/USDT",
    "ZEC": "ZEC/USDT",
    "XMR": "XMR/USDT",
    "CRV": "CRV/USDT",
    "SNX": "SNX/USDT",
    "LDO": "LDO/USDT",
    "PENDLE": "PENDLE/USDT",
    "LIT": "LIT/USDT",
}


@dataclass
class TradeSignal:
    symbol: str
    side: str
    confidence: float
    source: str
    source_symbol: str
    trader_count: int = 0
    entry_price: float | None = None
    size_usd: float = 0.0


class CopyTradeExecutor(ABC):
    def __init__(
        self,
        data_dir: str,
        dry_run: bool = True,
        interval: float = 60,
        stop_loss_pct: float = 30.0,
        take_profit_pct: float = 50.0,
        max_daily_loss_pct: float = 30.0,
        max_consecutive_losses: int = 3,
        total_capital: float = 1000.0,
    ):
        self.data_dir = data_dir
        self.dry_run = dry_run
        self.interval = interval
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_consecutive_losses = max_consecutive_losses
        self.total_capital = total_capital
        self.active_positions: dict[str, dict[str, Any]] = {}
        self._consecutive_losses = 0
        self._daily_pnl: float = 0.0
        self._circuit_breaker: bool = False
        self._circuit_breaker_reason: str = ""
        self._day_date: str = ""

    @abstractmethod
    def execute_signal(self, signal: TradeSignal) -> bool:
        ...

    @abstractmethod
    def close_position(self, symbol: str) -> bool:
        ...

    def collect_signals(self) -> list[TradeSignal]:
        return []

    def run_once(self) -> list[TradeSignal]:
        self._check_circuit_breaker()

        if self._circuit_breaker:
            logger.warning("Circuit breaker active — no new trades")
            return []

        # Kiểm tra SL/TP cho các positions đang mở
        for sym in list(self.active_positions.keys()):
            self._check_stop_loss(sym)

        signals = self.collect_signals()
        for signal in signals:
            key = signal.symbol
            existing = self.active_positions.get(key)
            if existing and existing["side"] != signal.side:
                logger.info("Signal flip: %s %s -> %s", key, existing["side"], signal.side)
                self.close_position(key)
            if signal.side == "hold":
                continue
            if key not in self.active_positions:
                self.execute_signal(signal)
        return signals

    def run_loop(self, iterations: int = 0) -> None:
        count = 0
        while True:
            count += 1
            try:
                self.run_once()
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                logger.exception("Executor loop failed: %s", exc)

            if iterations and count >= iterations:
                return
            time.sleep(self.interval)

    def _check_stop_loss(self, symbol: str) -> None:
        pos = self.active_positions.get(symbol)
        if not pos:
            return
        entry = pos.get("entry_price")
        if not entry:
            if self.dry_run:
                signal = pos.get("signal")
                entry = signal.entry_price if signal else None
            if not entry:
                return
        side = pos["side"]
        try:
            current = self._get_current_price(symbol)
        except Exception:
            return

        if side == "buy":
            pnl_pct = (current - entry) / entry * 100
            if pnl_pct <= -self.stop_loss_pct:
                logger.warning("SL HIT %s: entry=%.2f current=%.2f pnl=%.1f%%", symbol, entry, current, pnl_pct)
                self.close_position(symbol)
                self._consecutive_losses += 1
                self._daily_pnl += pnl_pct / 100 * (pos.get("size_usd") or 0)
            elif pnl_pct >= self.take_profit_pct:
                logger.info("TP HIT %s: entry=%.2f current=%.2f pnl=%.1f%%", symbol, entry, current, pnl_pct)
                self.close_position(symbol)
        else:  # sell
            pnl_pct = (entry - current) / entry * 100
            if pnl_pct <= -self.stop_loss_pct:
                logger.warning("SL HIT %s: entry=%.2f current=%.2f pnl=%.1f%%", symbol, entry, current, pnl_pct)
                self.close_position(symbol)
                self._consecutive_losses += 1
                self._daily_pnl += pnl_pct / 100 * (pos.get("size_usd") or 0)
            elif pnl_pct >= self.take_profit_pct:
                logger.info("TP HIT %s: entry=%.2f current=%.2f pnl=%.1f%%", symbol, entry, current, pnl_pct)
                self.close_position(symbol)

    def _get_current_price(self, symbol: str) -> float:
        raise NotImplementedError

    def _check_circuit_breaker(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._day_date:
            self._daily_pnl = 0.0
            self._consecutive_losses = 0
            self._day_date = today
            self._circuit_breaker = False
            self._circuit_breaker_reason = ""

        if self._circuit_breaker:
            logger.warning("Circuit breaker active: %s", self._circuit_breaker_reason)
            return

        if self._consecutive_losses >= self.max_consecutive_losses:
            self._circuit_breaker = True
            self._circuit_breaker_reason = f"{self._consecutive_losses} consecutive losses"
            logger.warning("CIRCUIT BREAKER: %s", self._circuit_breaker_reason)

        daily_loss_pct = abs(self._daily_pnl) / self.total_capital * 100 if self.total_capital > 0 else 0
        if daily_loss_pct >= self.max_daily_loss_pct:
            self._circuit_breaker = True
            self._circuit_breaker_reason = f"Daily loss ${self._daily_pnl:.2f} ({daily_loss_pct:.1f}%) >= {self.max_daily_loss_pct}% of ${self.total_capital:.0f} capital"
            logger.warning("CIRCUIT BREAKER: %s", self._circuit_breaker_reason)

    def _load_csv(self, name: str) -> list[dict[str, str]]:
        path = os.path.join(self.data_dir, name)
        if not os.path.exists(path):
            return []
        with open(path, newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def _log_trade(
        self,
        action: str,
        symbol: str,
        side: str,
        price: float | None,
        size_usd: float,
        source: str,
        source_symbol: str,
        trader_count: int = 0,
        tx_id: str = "",
        pnl: float | None = None,
        dry_run: bool = True,
    ) -> None:
        path = os.path.join(self.data_dir, "trade_history.csv")
        fieldnames = [
            "timestamp", "action", "symbol", "side", "price",
            "size_usd", "source", "source_symbol", "trader_count",
            "tx_id", "pnl", "dry_run",
        ]
        exists = os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            if not exists:
                writer.writeheader()
            writer.writerow({
                "timestamp": _now_iso(),
                "action": action,
                "symbol": symbol,
                "side": side,
                "price": price if price is not None else "",
                "size_usd": size_usd,
                "source": source,
                "source_symbol": source_symbol,
                "trader_count": trader_count,
                "tx_id": tx_id,
                "pnl": pnl if pnl is not None else "",
                "dry_run": str(dry_run),
            })


class BinanceFuturesExchange:
    """Small adapter for Binance USDT-M futures REST API."""

    def __init__(self):
        from config import Config

        self.api_key = os.getenv("BINANCE_FUTURES_API_KEY") or Config.BINANCE_API_KEY
        secret = os.getenv("BINANCE_FUTURES_SECRET_KEY") or Config.BINANCE_SECRET_KEY
        self.secret = secret.encode("utf-8")
        self.base_url = (
            "https://testnet.binancefuture.com"
            if Config.BINANCE_TESTNET
            else "https://fapi.binance.com"
        )
        self.session = requests.Session()
        self.session.headers.update({"X-MBX-APIKEY": self.api_key})
        self.markets = self._load_markets()
        logger.info(
            "Connected to Binance USDT-M Futures (%s) key=%.8s",
            "TESTNET" if Config.BINANCE_TESTNET else "MAINNET",
            self.api_key,
        )

    def _symbol(self, symbol: str) -> str:
        market = self.markets.get(symbol) or self.markets.get(f"{symbol}:USDT")
        if market:
            return str(market["id"])
        return symbol.replace("/", "").replace(":USDT", "")

    def _load_markets(self) -> dict[str, dict[str, Any]]:
        data = self._public("GET", "/fapi/v1/exchangeInfo")
        markets: dict[str, dict[str, Any]] = {}
        for item in data.get("symbols", []):
            if item.get("quoteAsset") != "USDT" or item.get("contractType") != "PERPETUAL":
                continue
            base = item.get("baseAsset")
            market_id = item.get("symbol")
            if not base or not market_id:
                continue
            step_size = "0.001"
            for flt in item.get("filters", []):
                if flt.get("filterType") == "LOT_SIZE":
                    step_size = flt.get("stepSize") or step_size
                    break
            market = {"id": market_id, "base": base, "step_size": step_size}
            markets[f"{base}/USDT"] = market
            markets[f"{base}/USDT:USDT"] = market
        return markets

    def _public(self, method: str, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = self.session.request(method, f"{self.base_url}{path}", params=params or {}, timeout=20)
        resp.raise_for_status()
        return resp.json()

    def _signed(self, method: str, path: str, params: dict[str, Any] | None = None) -> Any:
        import hashlib
        import hmac
        from urllib.parse import urlencode

        payload = dict(params or {})
        payload.setdefault("recvWindow", 10000)
        payload["timestamp"] = int(time.time() * 1000)
        query = urlencode(payload, doseq=True)
        signature = hmac.new(self.secret, query.encode("utf-8"), hashlib.sha256).hexdigest()
        payload["signature"] = signature
        resp = self.session.request(method, f"{self.base_url}{path}", params=payload, timeout=20)
        resp.raise_for_status()
        return resp.json()

    def fetch_balance(self) -> dict:
        data = self._signed("GET", "/fapi/v2/account")
        out: dict[str, dict[str, float]] = {}
        for asset in data.get("assets", []):
            name = asset.get("asset")
            if not name:
                continue
            out[name] = {
                "free": float(asset.get("availableBalance") or 0),
                "total": float(asset.get("walletBalance") or 0),
            }
        return out

    def get_free_balance(self, currency: str) -> float:
        balance = self.fetch_balance()
        return float(balance.get(currency, {}).get("free", 0))

    def get_ticker(self, symbol: str) -> dict:
        data = self._public("GET", "/fapi/v1/ticker/price", {"symbol": self._symbol(symbol)})
        if not data or "price" not in data:
            raise ValueError(f"No price data for {symbol} (symbol may not exist on exchange)")
        return {"last": float(data["price"])}

    def _amount_to_precision(self, symbol: str, amount: float) -> float:
        from decimal import Decimal, ROUND_DOWN

        market = self.markets.get(symbol) or self.markets.get(f"{symbol}:USDT")
        step = Decimal(str((market or {}).get("step_size", "0.001")))
        value = Decimal(str(amount))
        return float((value / step).to_integral_value(rounding=ROUND_DOWN) * step)

    def create_market_order(
        self,
        side: str,
        amount: float,
        symbol: str,
        reduce_only: bool = False,
    ) -> dict:
        order_symbol = self._symbol(symbol)
        precise_amount = self._amount_to_precision(symbol, amount)
        params: dict[str, Any] = {
            "symbol": order_symbol,
            "side": side.upper(),
            "type": "MARKET",
            "quantity": f"{precise_amount:.12f}".rstrip("0").rstrip("."),
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        logger.info("Placing FUTURES %s order: %s %.6f reduce_only=%s",
                    side.upper(), order_symbol, precise_amount, reduce_only)
        return self._signed("POST", "/fapi/v1/order", params)


class BinanceCopyExecutor(CopyTradeExecutor):
    def __init__(
        self,
        data_dir: str,
        dry_run: bool = True,
        interval: float = 60,
        max_positions: int = 3,
        position_size_usd: float = 50,
        min_confidence: float = 0.60,
        stop_loss_pct: float = 30.0,
        take_profit_pct: float = 50.0,
        max_daily_loss_pct: float = 30.0,
        max_consecutive_losses: int = 3,
        total_capital: float = 1000.0,
    ):
        super().__init__(data_dir, dry_run, interval, stop_loss_pct, take_profit_pct, max_daily_loss_pct, max_consecutive_losses, total_capital)
        self.max_positions = max_positions
        self.position_size_usd = position_size_usd
        self.min_confidence = min_confidence
        self.exchange = None
        self._processed_sigs: set[str] = set()

    def _init_exchange(self) -> None:
        if self.exchange is not None:
            return
        if self.dry_run:
            return
        try:
            self.exchange = BinanceFuturesExchange()
            usdt = self.exchange.get_free_balance("USDT")
            logger.info(
                "Binance Futures connected. USDT balance: %.2f (key=%.8s...)",
                usdt, self.exchange.api_key,
            )
        except Exception as exc:
            logger.warning("Binance Futures init failed (dry-run will simulate): %s", exc)

    def collect_signals(self) -> list[TradeSignal]:
        return self._collect_macro_sentiment()

    def _collect_macro_sentiment(self) -> list[TradeSignal]:
        selection = self._load_csv("wallet_selection.csv")
        if not selection:
            return []

        selected_wallets = {row.get("wallet", "") for row in selection if row.get("wallet")}

        perf = self._load_csv("wallet_performance.csv")
        if not perf:
            return []

        long_count = 0
        short_count = 0
        token_signals: dict[str, dict[str, int]] = {}
        for row in perf:
            wallet = row.get("wallet", "")
            if wallet not in selected_wallets:
                continue
            top = (row.get("top_tokens") or row.get("tokens", "")).upper()
            for t in [x.strip() for x in top.replace(",", " ").split() if x.strip()]:
                binance_sym = SYMBOL_MAP.get(t, "")
                if not binance_sym:
                    continue
                token_signals.setdefault(t, {"buy": 0, "sell": 0, "wallets": set()})
                token_signals[t]["buy"] += 1
                token_signals[t]["wallets"].add(wallet)

        if not token_signals:
            return []

        signals = []
        for token, data in token_signals.items():
            total = len(data["wallets"])
            if total < 2:
                continue
            binance_sym = SYMBOL_MAP.get(token, "")
            confidence = min(data["buy"] / total, 0.85)
            if confidence < self.min_confidence:
                continue
            signals.append(TradeSignal(
                symbol=binance_sym,
                side="buy",
                confidence=confidence,
                source="macro_sentiment",
                source_symbol=token,
                trader_count=total,
                size_usd=self.position_size_usd,
            ))

        signals.sort(key=lambda s: s.trader_count, reverse=True)
        if signals:
            logger.info("Macro sentiment: %d tokens from %d wallets, top=%s (%d wallets, %.0f%%)",
                         len(signals), len(selected_wallets),
                         signals[0].source_symbol, signals[0].trader_count,
                         signals[0].confidence * 100)
        return signals[:3]

    def execute_signal(self, signal: TradeSignal) -> bool:
        self._init_exchange()
        if len(self.active_positions) >= self.max_positions:
            logger.info("Max positions reached (%d), skip %s %s", self.max_positions, signal.side, signal.symbol)
            return False

        if self.dry_run:
            logger.info(
                "[DRY-RUN] %s %s $%.0f (confidence=%.0f%%, traders=%d, source=%s)",
                signal.side.upper(), signal.symbol, signal.size_usd,
                signal.confidence * 100, signal.trader_count, signal.source_symbol,
            )
            self.active_positions[signal.symbol] = {
                "side": signal.side,
                "entry_time": _now_iso(),
                "signal": signal,
            }
            self._log_trade("open", signal.symbol, signal.side, None, signal.size_usd,
                            signal.source, signal.source_symbol, signal.trader_count, dry_run=True)
            return True

        try:
            ticker = self.exchange.get_ticker(signal.symbol)
            price = ticker["last"]
            usdt_balance = self.exchange.get_free_balance("USDT")
            if usdt_balance < signal.size_usd:
                logger.warning("Insufficient USDT: need %.2f, have %.2f", signal.size_usd, usdt_balance)
                return False

            amount = signal.size_usd / price
            order = self.exchange.create_market_order(signal.side, amount, signal.symbol)
            self.active_positions[signal.symbol] = {
                "side": signal.side,
                "entry_price": price,
                "entry_time": _now_iso(),
                "amount": amount,
                "order": order,
                "signal": signal,
            }
            logger.info("EXECUTED %s %s %.6f @ %.2f", signal.side.upper(), signal.symbol, amount, price)
            self._log_trade("open", signal.symbol, signal.side, price, signal.size_usd,
                            signal.source, signal.source_symbol, signal.trader_count,
                            tx_id=str(order.get("id", "")), dry_run=False)
            return True
        except Exception as exc:
            logger.error("Execute failed %s %s: %s", signal.side, signal.symbol, exc)
            return False

    def _get_current_price(self, symbol: str) -> float:
        if self.exchange is None:
            self._init_exchange()
        if self.exchange is None:
            try:
                import ccxt
                pub = ccxt.binance({"enableRateLimit": True})
                ticker = pub.fetch_ticker(symbol)
                return float(ticker["last"])
            except Exception:
                return 0.0
        try:
            ticker = self.exchange.get_ticker(symbol)
            return ticker["last"]
        except Exception:
            return 0.0

    def close_position(self, symbol: str) -> bool:
        pos = self.active_positions.get(symbol)
        if not pos:
            return False
        close_side = "sell" if pos["side"] == "buy" else "buy"

        if self.dry_run:
            logger.info("[DRY-RUN] CLOSE %s %s", close_side.upper(), symbol)
            self._log_trade("close_dry", symbol, close_side, None, 0,
                            "binance", symbol.split("/")[0], dry_run=True)
            self.active_positions.pop(symbol, None)
            return True

        try:
            base = symbol.split("/")[0]
            amount = self.exchange.get_free_balance(base)
            if amount <= 0:
                logger.warning("No %s balance to close", base)
                self.active_positions.pop(symbol, None)
                return False
            self.exchange.create_market_order(close_side, amount, symbol)
            self.active_positions.pop(symbol, None)
            logger.info("CLOSED %s %s %.6f", close_side.upper(), symbol, amount)
            return True
        except Exception as exc:
            logger.error("Close failed %s: %s", symbol, exc)
            return False


class HyperliquidCopyExecutor(BinanceCopyExecutor):
    def __init__(
        self,
        data_dir: str,
        dry_run: bool = True,
        interval: float = 60,
        max_positions: int = 3,
        position_size_usd: float = 50,
        min_confidence: float = 0.70,
        min_delta_notional: float = 1000.0,
        recent_seconds: int = 900,
        trusted_wallets_csv: str = "hyperliquid_leaderboard.csv",
        stop_loss_pct: float = 30.0,
        take_profit_pct: float = 50.0,
        max_daily_loss_pct: float = 30.0,
        max_consecutive_losses: int = 3,
        total_capital: float = 1000.0,
    ):
        super().__init__(
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
        )
        self.min_delta_notional = min_delta_notional
        self.recent_seconds = recent_seconds
        self.trusted_wallets_csv = trusted_wallets_csv
        self.copy_state_path = os.path.join(self.data_dir, "hyperliquid_copy_state.json")
        self._processed_sigs.update(self._load_processed_state())

    def _init_exchange(self) -> None:
        if self.exchange is not None:
            return
        if self.dry_run:
            return
        try:
            self.exchange = BinanceFuturesExchange()
            balance = self.exchange.fetch_balance()
            usdt = float(balance.get("USDT", {}).get("free", 0))
            logger.info("Binance futures connected. USDT futures balance: %.2f", usdt)
        except Exception as exc:
            logger.warning("Binance futures init failed: %s", exc)

    def close_position(self, symbol: str) -> bool:
        pos = self.active_positions.get(symbol)
        if not pos:
            return False
        close_side = "sell" if pos["side"] == "buy" else "buy"

        if self.dry_run:
            logger.info("[DRY-RUN] FUTURES CLOSE %s %s", close_side.upper(), symbol)
            self._log_trade("close_dry", symbol, close_side, None, 0,
                            "hyperliquid", symbol.split("/")[0], dry_run=True)
            self.active_positions.pop(symbol, None)
            return True

        self._init_exchange()
        if self.exchange is None:
            return False

        try:
            amount = float(pos.get("amount") or 0)
            if amount <= 0:
                logger.warning("No tracked futures amount to close for %s", symbol)
                self.active_positions.pop(symbol, None)
                return False
            order = self.exchange.create_market_order(close_side, amount, symbol, reduce_only=True)
            self.active_positions.pop(symbol, None)
            logger.info("FUTURES CLOSED %s %s %.6f", close_side.upper(), symbol, amount)
            self._log_trade("close", symbol, close_side, None, 0,
                            "hyperliquid", symbol.split("/")[0],
                            tx_id=str(order.get("id", "")), dry_run=False)
            return True
        except Exception as exc:
            logger.error("Futures close failed %s: %s", symbol, exc)
            return False

    def collect_signals(self) -> list[TradeSignal]:
        position_signals = self._collect_hyperliquid_position_signals()
        if position_signals:
            self._mark_recent_hyperliquid_positions_processed()
            self._mark_recent_hyperliquid_fills_processed()
            return position_signals
        return self._collect_hyperliquid_fill_signals()

    def _collect_hyperliquid_position_signals(self) -> list[TradeSignal]:
        events = self._load_csv("hyperliquid_position_events.csv")
        if not events:
            logger.debug("Need hyperliquid_position_events.csv")
            return []

        trusted = self._trusted_hyperliquid_wallets()
        cutoff = time.time() - self.recent_seconds
        signals: list[TradeSignal] = []
        seen_symbols: set[str] = set()

        for row in reversed(events):
            wallet = (row.get("wallet") or "").lower()
            if trusted and wallet not in trusted:
                continue

            event_id = "|".join([
                wallet,
                row.get("collected_at", ""),
                row.get("event_type", ""),
                row.get("coin", ""),
                row.get("current_size", ""),
            ])
            if event_id in self._processed_sigs:
                continue

            ts = _parse_iso_to_ts(row.get("collected_at", ""))
            if ts is None or ts < cutoff:
                continue

            event_type = (row.get("event_type") or "").lower()
            if event_type not in ("open", "increase", "flip"):
                continue

            coin = (row.get("coin") or "").upper()
            symbol = _hyperliquid_coin_to_symbol(coin)
            if not symbol or symbol in seen_symbols:
                continue

            delta_notional = _event_delta_notional(row)
            if delta_notional < self.min_delta_notional:
                continue

            hl_side = (row.get("side") or "").lower()
            if hl_side not in ("long", "short"):
                continue
            side = "buy" if hl_side == "long" else "sell"

            confidence = self._wallet_confidence(wallet)
            if confidence < self.min_confidence:
                continue

            self._mark_processed(event_id)
            seen_symbols.add(symbol)
            signals.append(TradeSignal(
                symbol=symbol,
                side=side,
                confidence=confidence,
                source="hyperliquid_position_copy",
                source_symbol=coin,
                trader_count=1,
                entry_price=_to_float(row.get("entry_price")),
                size_usd=self.position_size_usd,
            ))

            logger.info(
                "Hyperliquid copy signal: %s %s from %s %s delta≈$%.0f conf=%.0f%%",
                side.upper(), symbol, wallet[:10], event_type, delta_notional, confidence * 100,
            )

        return signals

    def _mark_recent_hyperliquid_positions_processed(self) -> None:
        events = self._load_csv("hyperliquid_position_events.csv")
        if not events:
            return
        cutoff = time.time() - self.recent_seconds
        changed = False
        for row in reversed(events):
            ts = _parse_iso_to_ts(row.get("collected_at", ""))
            if ts is None or ts < cutoff:
                continue
            event_id = _hyperliquid_position_event_id(row)
            if event_id and event_id not in self._processed_sigs:
                self._processed_sigs.add(event_id)
                changed = True
        if changed:
            self._save_processed_state()

    def _collect_hyperliquid_fill_signals(self) -> list[TradeSignal]:
        fills = self._load_csv("hyperliquid_fill_events.csv")
        if not fills:
            return []

        trusted = self._trusted_hyperliquid_wallets()
        cutoff = time.time() - self.recent_seconds
        signals: list[TradeSignal] = []
        seen_symbols: set[str] = set()

        for row in reversed(fills):
            wallet = (row.get("wallet") or "").lower()
            if trusted and wallet not in trusted:
                continue
            tid = row.get("tid") or row.get("hash") or ""
            if tid in self._processed_sigs:
                continue
            ts = _parse_iso_to_ts(row.get("fill_time", ""))
            if ts is None or ts < cutoff:
                continue

            direction = (row.get("direction") or "").lower()
            if not direction.startswith("open"):
                continue

            coin = (row.get("coin") or "").upper()
            symbol = _hyperliquid_coin_to_symbol(coin)
            if not symbol or symbol in seen_symbols:
                continue

            price = _to_float(row.get("price"))
            size = _to_float(row.get("size"))
            if price * size < self.min_delta_notional:
                continue

            side = "buy" if "long" in direction else "sell" if "short" in direction else ""
            if not side:
                continue

            confidence = self._wallet_confidence(wallet)
            if confidence < self.min_confidence:
                continue

            self._mark_processed(tid)
            seen_symbols.add(symbol)
            signals.append(TradeSignal(
                symbol=symbol,
                side=side,
                confidence=confidence,
                source="hyperliquid_fill_copy",
                source_symbol=coin,
                trader_count=1,
                entry_price=price,
                size_usd=self.position_size_usd,
            ))

        return signals

    def _mark_recent_hyperliquid_fills_processed(self) -> None:
        fills = self._load_csv("hyperliquid_fill_events.csv")
        if not fills:
            return
        cutoff = time.time() - self.recent_seconds
        changed = False
        for row in reversed(fills):
            tid = row.get("tid") or row.get("hash") or ""
            if not tid or tid in self._processed_sigs:
                continue
            ts = _parse_iso_to_ts(row.get("fill_time", ""))
            if ts is None or ts < cutoff:
                continue
            self._processed_sigs.add(tid)
            changed = True
        if changed:
            self._save_processed_state()

    def _trusted_hyperliquid_wallets(self) -> set[str]:
        rows = self._load_csv(self.trusted_wallets_csv)
        if not rows:
            return set()
        trusted = set()
        for row in rows:
            wallet = (row.get("wallet") or row.get("ethAddress") or "").lower()
            if wallet:
                trusted.add(wallet)
        return trusted

    def _wallet_confidence(self, wallet: str) -> float:
        rows = self._load_csv(self.trusted_wallets_csv)
        for row in rows:
            row_wallet = (row.get("wallet") or row.get("ethAddress") or "").lower()
            if row_wallet != wallet:
                continue
            roi = _to_float(row.get("roi"))
            volume = _to_float(row.get("volume"))
            confidence = 0.70
            if roi > 1:
                confidence += 0.10
            if volume > 1_000_000:
                confidence += 0.10
            if str(row.get("active_24h")).lower() == "true":
                confidence += 0.05
            return min(confidence, 0.95)
        return 0.75

    def _load_processed_state(self) -> set[str]:
        if not os.path.exists(self.copy_state_path):
            return set()
        try:
            with open(self.copy_state_path, encoding="utf-8") as handle:
                data = json.load(handle)
            items = data.get("processed_ids", []) if isinstance(data, dict) else []
            return {str(item) for item in items}
        except Exception:
            return set()

    def _mark_processed(self, event_id: str) -> None:
        self._processed_sigs.add(event_id)
        self._save_processed_state()

    def _save_processed_state(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.copy_state_path), exist_ok=True)
            recent = list(dict.fromkeys(list(self._processed_sigs)[-5000:]))
            with open(self.copy_state_path, "w", encoding="utf-8") as handle:
                json.dump({"updated_at": _now_iso(), "processed_ids": recent}, handle, indent=2, sort_keys=True)
        except Exception as exc:
            logger.debug("Could not save Hyperliquid copy state: %s", exc)


class CopyTradeOrchestrator:
    def __init__(self, data_dir: str, dry_run: bool = True):
        self.data_dir = data_dir
        self.dry_run = dry_run
        self.executors: list[CopyTradeExecutor] = []

    def add(self, executor: CopyTradeExecutor) -> None:
        self.executors.append(executor)

    def run_once(self) -> list[TradeSignal]:
        all_signals = []
        for executor in self.executors:
            signals = executor.run_once()
            all_signals.extend(signals)
        return all_signals

    def run_loop(self, iterations: int = 0) -> None:
        import threading
        threads = []
        for executor in self.executors:
            t = threading.Thread(target=executor.run_loop, args=(iterations,), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()


def build_binance_executor(
    data_dir: str,
    dry_run: bool = True,
    interval: float = 60,
    max_positions: int = 3,
    position_size_usd: float = 50,
    min_confidence: float = 0.60,
    stop_loss_pct: float = 5.0,
    take_profit_pct: float = 10.0,
    max_daily_loss_pct: float = 10.0,
    max_consecutive_losses: int = 3,
    total_capital: float = 1000.0,
) -> BinanceCopyExecutor:
    return BinanceCopyExecutor(
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
    )


def build_hyperliquid_executor(
    data_dir: str,
    dry_run: bool = True,
    interval: float = 60,
    max_positions: int = 3,
    position_size_usd: float = 50,
    min_confidence: float = 0.70,
    min_delta_notional: float = 1000.0,
    recent_seconds: int = 900,
    trusted_wallets_csv: str = "hyperliquid_leaderboard.csv",
    stop_loss_pct: float = 30.0,
    take_profit_pct: float = 50.0,
    max_daily_loss_pct: float = 30.0,
    max_consecutive_losses: int = 3,
    total_capital: float = 1000.0,
) -> HyperliquidCopyExecutor:
    return HyperliquidCopyExecutor(
        data_dir=data_dir,
        dry_run=dry_run,
        interval=interval,
        max_positions=max_positions,
        position_size_usd=position_size_usd,
        min_confidence=min_confidence,
        min_delta_notional=min_delta_notional,
        recent_seconds=recent_seconds,
        trusted_wallets_csv=trusted_wallets_csv,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        max_daily_loss_pct=max_daily_loss_pct,
        max_consecutive_losses=max_consecutive_losses,
        total_capital=total_capital,
    )


def _to_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except ValueError:
        return 0.0


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except ValueError:
        return None


def _hyperliquid_coin_to_symbol(coin: str) -> str:
    if not coin or coin.startswith("@") or ":" in coin or coin.startswith("#"):
        return ""
    return SYMBOL_MAP.get(coin.upper(), "")


def _event_delta_notional(row: dict[str, str]) -> float:
    size_delta = abs(_to_float(row.get("size_delta")))
    entry = _to_float(row.get("entry_price"))
    if size_delta and entry:
        return size_delta * entry

    previous = abs(_to_float(row.get("previous_size")))
    current = abs(_to_float(row.get("current_size")))
    value = _to_float(row.get("position_value"))
    if value and current:
        return value * abs(current - previous) / current
    return value


def _hyperliquid_position_event_id(row: dict[str, str]) -> str:
    wallet = (row.get("wallet") or "").lower()
    return "|".join([
        wallet,
        row.get("collected_at", ""),
        row.get("event_type", ""),
        row.get("coin", ""),
        row.get("current_size", ""),
    ])


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso_to_ts(iso_str: str) -> float | None:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None
