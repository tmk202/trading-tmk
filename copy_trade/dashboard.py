from __future__ import annotations

import csv
import json
import os
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from copy_trade.models import utc_now_iso
from copy_trade.storage import CopyTradeStore


def _import_provider():
    from copy_trade.providers import make_provider
    return make_provider


def _import_analyzer():
    from copy_trade.analyzer import build_consensus, select_traders
    return build_consensus, select_traders


def _import_tracker():
    from copy_trade.solana_tracker import SolanaRpcTracker, load_wallets_from_performance, load_state, save_state
    return SolanaRpcTracker, load_wallets_from_performance, load_state, save_state


class CopyTradeDashboard:
    def __init__(
        self,
        data_dir: str,
        refresh: float = 5.0,
        rows: int = 10,
        collect: bool = False,
        collect_interval: float = 120.0,
        track_interval: float = 30.0,
        okx_url: str = "https://web3.okx.com/copy-trade/leaderboard/solana",
        okx_per_rank_limit: int = 100,
        okx_max_wallets: int = 300,
        track_wallet_limit: int = 10,
        track_tx_limit: int = 8,
        track_min_win_rate: float = 55,
        track_min_trades: int = 100,
        track_min_pnl: float = 5000,
        rpc_url: str = "https://solana-rpc.publicnode.com",
    ):
        self.data_dir = data_dir
        self.refresh = refresh
        self.rows = rows
        self.collect = collect
        self.collect_interval = collect_interval
        self.track_interval = track_interval
        self.okx_url = okx_url
        self.okx_per_rank_limit = okx_per_rank_limit
        self.okx_max_wallets = okx_max_wallets
        self.track_wallet_limit = track_wallet_limit
        self.track_tx_limit = track_tx_limit
        self.track_min_win_rate = track_min_win_rate
        self.track_min_trades = track_min_trades
        self.track_min_pnl = track_min_pnl
        self.rpc_url = rpc_url
        self.store = CopyTradeStore(data_dir)
        self._stop = threading.Event()
        self._last_collect = 0.0
        self._last_track = 0.0
        self._pipeline_ok = True
        self._pipeline_msg = ""

    def run(self, iterations: int = 0) -> None:
        if self.collect:
            t = threading.Thread(target=self._pipeline_loop, daemon=True)
            t.start()

        iteration = 0
        with Live(self.render(), refresh_per_second=2, screen=True) as live:
            while not self._stop.is_set():
                iteration += 1
                time.sleep(self.refresh)
                live.update(self.render())
                if iterations and iteration >= iterations:
                    break

    def print_once(self) -> None:
        Console().print(self.render())

    def stop(self) -> None:
        self._stop.set()

    # ── Pipeline ────────────────────────────────────────────

    def _pipeline_loop(self) -> None:
        while not self._stop.is_set():
            now = time.time()
            try:
                if now - self._last_collect >= self.collect_interval:
                    self._run_okx_sweep()
                    self._run_wallet_performance()
                    self._run_select_wallets()
                    self._run_consensus()
                    self._last_collect = time.time()

                if now - self._last_track >= self.track_interval:
                    self._run_track_wallets()
                    self._last_track = time.time()
            except Exception as exc:
                self._pipeline_ok = False
                self._pipeline_msg = f"Pipeline error: {exc}"
            time.sleep(5)

    def _run_okx_sweep(self) -> None:
        make_provider = _import_provider()
        from copy_trade.models import TraderSnapshot, PositionSnapshot

        rank_modes = ["pnl", "roi", "win_rate", "volume", "tx"]
        periods = ["30d"]
        seen = set()
        traders: list[TraderSnapshot] = []
        positions: list[PositionSnapshot] = []

        for period in periods:
            for rank_by in rank_modes:
                try:
                    provider = make_provider(
                        "okx_web3",
                        okx_url=self.okx_url,
                        okx_chain_id="501",
                        okx_rank_by=rank_by,
                        okx_period=period,
                    )
                    batch = provider.fetch_traders(limit=self.okx_per_rank_limit)
                except Exception:
                    continue
                for trader in batch:
                    if trader.trader_id in seen:
                        continue
                    seen.add(trader.trader_id)
                    traders.append(trader)
                    try:
                        positions.extend(provider.fetch_positions(trader.trader_id))
                    except Exception:
                        pass
                    if len(traders) >= self.okx_max_wallets:
                        break
                if len(traders) >= self.okx_max_wallets:
                    break

        if traders:
            self.store.append_csv("trader_daily_stats.csv", traders)
            self.store.append_jsonl("trader_daily_stats.jsonl", traders)
        if positions:
            self.store.append_csv("trader_positions.csv", positions)
            self.store.append_jsonl("trader_positions.jsonl", positions)
        self._pipeline_msg = f"Collected {len(traders)} traders, {len(positions)} positions"

    def _run_wallet_performance(self) -> None:
        csv_path = os.path.join(self.data_dir, "trader_daily_stats.csv")
        if not os.path.exists(csv_path):
            return
        from copy_trade_lab import cmd_wallet_performance
        import argparse

        args = argparse.Namespace(
            traders_csv=csv_path,
            platform="okx_web3",
            top=150,
            rows=30,
            min_trades=5,
            min_pnl=100,
            min_win_rate=0,
            output="",
            data_dir=self.data_dir,
        )
        try:
            cmd_wallet_performance(args)
        except Exception:
            pass

    def _run_select_wallets(self) -> None:
        perf_csv = os.path.join(self.data_dir, "wallet_performance.csv")
        if not os.path.exists(perf_csv):
            return
        from copy_trade_lab import cmd_select_wallets
        import argparse

        args = argparse.Namespace(
            perf_csv=perf_csv,
            top=0,
            rows=50,
            min_win_rate=30,
            max_drawdown=100,
            min_trades=5,
            min_pnl=500,
            output="",
            data_dir=self.data_dir,
        )
        try:
            cmd_select_wallets(args)
        except Exception:
            pass

    def _run_consensus(self) -> None:
        positions_csv = os.path.join(self.data_dir, "trader_positions.csv")
        selection_csv = os.path.join(self.data_dir, "wallet_selection.csv")
        if not os.path.exists(positions_csv):
            return
        from copy_trade_lab import cmd_consensus
        import argparse

        traders_csv = selection_csv if os.path.exists(selection_csv) else ""
        args = argparse.Namespace(
            traders_csv=traders_csv,
            positions_csv=positions_csv,
            limit=1000,
            top=50,
            threshold=0.60,
            max_drawdown=None,
            min_win_rate=None,
            min_copy_days=None,
            rows=30,
            data_dir=self.data_dir,
        )
        try:
            cmd_consensus(args)
        except Exception:
            pass

    def _run_track_wallets(self) -> None:
        perf_csv = os.path.join(self.data_dir, "wallet_performance.csv")
        if not os.path.exists(perf_csv):
            return
        SolanaRpcTracker, load_wallets_from_performance, load_state, save_state = _import_tracker()

        wallets = load_wallets_from_performance(
            perf_csv,
            limit=self.track_wallet_limit,
            min_win_rate=self.track_min_win_rate,
            min_trades=self.track_min_trades,
            min_pnl=self.track_min_pnl,
        )
        if not wallets:
            return

        tracker = SolanaRpcTracker(rpc_url=self.rpc_url, sleep_s=0.2)
        state_path = os.path.join(self.data_dir, "solana_tracker_state.json")
        state = load_state(state_path)

        all_events = []
        for wallet in wallets:
            seen = set(state.get(wallet, []))
            try:
                events, newest = tracker.collect_wallet(
                    wallet=wallet, limit=self.track_tx_limit, seen_signatures=seen,
                )
            except Exception:
                continue
            if newest:
                state[wallet] = list(dict.fromkeys(newest + state.get(wallet, [])))[:200]
            all_events.extend(events)

        if all_events:
            self.store.append_csv("wallet_trade_events.csv", all_events)
            self.store.append_jsonl("wallet_trade_events.jsonl", all_events)
        save_state(state_path, state)

    # ── Panels ──────────────────────────────────────────────

    def render(self) -> Group:
        trader_rows = _read_csv(os.path.join(self.data_dir, "trader_daily_stats.csv"))
        perf_rows = _read_csv(os.path.join(self.data_dir, "wallet_performance.csv"))
        trade_rows = _read_csv(os.path.join(self.data_dir, "wallet_trade_events.csv"))
        summary_rows = _read_csv(os.path.join(self.data_dir, "wallet_trade_summary.csv"))
        position_rows = _read_csv(os.path.join(self.data_dir, "trader_positions.csv"))
        consensus_rows = _read_csv(os.path.join(self.data_dir, "consensus_signals.csv"))
        selected_rows = _read_csv(os.path.join(self.data_dir, "wallet_selection.csv"))
        trade_history = _read_csv(os.path.join(self.data_dir, "trade_history.csv"))
        alert_rows = _read_jsonl_tail(os.path.join(self.data_dir, "realtime_alerts.jsonl"), limit=200)
        state = _read_json(os.path.join(self.data_dir, "solana_tracker_state.json"))

        return Group(
            self._header(trader_rows, perf_rows, trade_rows, state),
            self._pipeline_status(),
            self._pnl_summary(trade_history),
            self._selected_wallets(selected_rows),
            self._top_wallets(perf_rows),
            self._trade_flow(summary_rows, trade_rows),
            self._token_consensus(position_rows, trade_rows),
            self._consensus_signals(consensus_rows),
            self._recent_alerts(alert_rows, trade_rows),
            self._commands(),
        )

    def _pipeline_status(self) -> Panel:
        text = Text()
        if self.collect:
            status = "ON" if self._pipeline_ok else "ERROR"
            style = "green" if self._pipeline_ok else "red"
            text.append(f"Auto-collect: ", style="bold")
            text.append(status, style=style)
            text.append(f"  {self._pipeline_msg}", style="dim")
        else:
            text.append("Auto-collect: OFF   (use --collect to enable live pipeline)", style="dim")
        return Panel(Align.left(text), title="Pipeline Status", border_style="bold" if self.collect else "dim")

    def _pnl_summary(self, rows: list[dict[str, str]]) -> Panel:
        live_opens = [r for r in rows if r.get("action") == "open" and r.get("dry_run") == "False"]
        dry_opens = [r for r in rows if r.get("action") == "open" and r.get("dry_run") == "True"]
        closed = [r for r in rows if "close" in (r.get("action") or "")]

        text = Text()
        text.append(f"Trades: ", style="bold")
        text.append(f"{len(live_opens)} live  ", style="green")
        text.append(f"{len(dry_opens)} dry-run  ", style="dim")
        text.append(f"{len(closed)} closed\n")

        if live_opens:
            text.append("Open positions: ", style="bold")
            for t in live_opens:
                sym = t.get("symbol", "")
                side = t.get("side", "")
                price = _float(t.get("price"))
                size = _float(t.get("size_usd"))
                ts = t.get("timestamp", "")[11:19]
                entry_str = f"@{price:.2f}" if price else ""
                text.append(f"\n  {ts} {side.upper():4} {sym:<10} ${size:.0f} {entry_str}", style="cyan")
        else:
            text.append("\nNo live positions", style="dim")

        return Panel(Align.left(text), title="Trade PnL", border_style="bold green" if live_opens else "dim")

    def _header(
        self,
        trader_rows: list[dict[str, str]],
        perf_rows: list[dict[str, str]],
        trade_rows: list[dict[str, str]],
        state: dict[str, Any],
    ) -> Panel:
        unique_traders = len({row.get("trader_id") for row in trader_rows if row.get("trader_id")})
        tracked_wallets = len(state) if isinstance(state, dict) else 0
        trade_events = len(trade_rows)
        last_trade = max((row.get("block_time", "") for row in trade_rows), default="")
        text = Text()
        text.append("Copy Trade Control Room\n", style="bold cyan")
        text.append(f"data_dir: {self.data_dir}\n", style="dim")
        text.append(
            f"wallet universe: {unique_traders}   "
            f"performance rows: {len(perf_rows)}   "
            f"tracked wallets: {tracked_wallets}   "
            f"trade events: {trade_events}\n"
        )
        text.append(f"last trade: {last_trade or '-'}   refreshed: {_now_local()}")
        return Panel(text, title="Project Status", border_style="cyan")

    def _selected_wallets(self, rows: list[dict[str, str]]) -> Panel:
        table = Table(expand=True)
        table.add_column("#")
        table.add_column("Wallet", no_wrap=True)
        table.add_column("Name")
        table.add_column("Score", justify="right")
        table.add_column("Win", justify="right")
        table.add_column("Trades", justify="right")
        table.add_column("PnL", justify="right")
        table.add_column("Top tokens")

        for idx, row in enumerate(rows[: self.rows], 1):
            wr = _float(row.get("win_rate_pct"))
            table.add_row(
                str(idx),
                _short(row.get("wallet")),
                row.get("nickname", "")[:12],
                _num(row.get("score"), 2),
                _pct(wr),
                str(int(_float(row.get("tx")))),
                _num(row.get("pnl"), 0),
                (row.get("top_tokens") or "")[:42],
                style="green" if wr >= 50 else "yellow" if wr >= 30 else "",
            )
        if not rows:
            table.add_row("No wallets selected", "", "", "", "", "", "", "", style="dim")
        return Panel(table, title="Selected Wallets (Copy Target)", border_style="bold cyan")

    def _top_wallets(self, rows: list[dict[str, str]]) -> Panel:
        table = Table(expand=True)
        table.add_column("Wallet", no_wrap=True)
        table.add_column("Name")
        table.add_column("Score", justify="right")
        table.add_column("Win", justify="right")
        table.add_column("Loss", justify="right")
        table.add_column("Tx", justify="right")
        table.add_column("PnL", justify="right")
        table.add_column("DD", justify="right")
        table.add_column("Top tokens")

        for row in rows[: self.rows]:
            win = _float(row.get("win_rate_pct"))
            loss = _float(row.get("estimated_loss_rate_pct"))
            table.add_row(
                _short(row.get("wallet")),
                row.get("nickname", ""),
                _num(row.get("score"), 2),
                _pct(win),
                _pct(loss),
                str(int(_float(row.get("tx")))),
                _num(row.get("pnl"), 0),
                _num(row.get("pnl_history_max_drawdown"), 0),
                (row.get("top_tokens") or "")[:42],
                style=_wallet_style(win, loss),
            )
        return Panel(table, title="Wallet Performance", border_style="green")

    def _trade_flow(self, summary_rows: list[dict[str, str]], trade_rows: list[dict[str, str]]) -> Panel:
        latest_by_wallet = {}
        for row in trade_rows:
            wallet = row.get("wallet", "")
            if wallet:
                latest_by_wallet[wallet] = row

        table = Table(expand=True)
        table.add_column("Wallet", no_wrap=True)
        table.add_column("Events", justify="right")
        table.add_column("Buy", justify="right")
        table.add_column("Sell", justify="right")
        table.add_column("Net SOL", justify="right")
        table.add_column("Latest")
        table.add_column("Tokens")

        for row in summary_rows[: self.rows]:
            wallet = row.get("wallet", "")
            latest = latest_by_wallet.get(wallet, {})
            action = latest.get("action", "-").upper()
            token = latest.get("token_symbol") or latest.get("token_mint", "")[:8]
            table.add_row(
                _short(wallet),
                row.get("event_count", "0"),
                row.get("buy_count", "0"),
                row.get("sell_count", "0"),
                _num(row.get("quote_net"), 4),
                f"{action} {token}",
                (row.get("tokens") or "")[:48],
                style=_net_style(_float(row.get("quote_net"))),
            )
        return Panel(table, title="Tracked Trade Flow", border_style="yellow")

    def _token_consensus(self, position_rows: list[dict[str, str]], trade_rows: list[dict[str, str]]) -> Panel:
        okx_positions = [row for row in position_rows if row.get("platform") == "okx_web3"]
        token_wallets: dict[str, set[str]] = {}
        for row in okx_positions:
            token = (row.get("symbol") or "").upper()
            wallet = row.get("trader_id") or ""
            if token and wallet:
                token_wallets.setdefault(token, set()).add(wallet)

        token_pnl: dict[str, float] = {}
        for row in okx_positions:
            token = (row.get("symbol") or "").upper()
            pnl = _float(row.get("pnl"))
            if token and pnl:
                token_pnl[token] = token_pnl.get(token, 0.0) + pnl

        recent_buys = Counter()
        recent_sells = Counter()
        for row in trade_rows[-200:]:
            token = (row.get("token_symbol") or "").upper()
            if not token:
                continue
            if row.get("action") == "buy":
                recent_buys[token] += 1
            elif row.get("action") == "sell":
                recent_sells[token] += 1

        ranked = sorted(token_wallets.items(), key=lambda item: len(item[1]), reverse=True)
        table = Table(expand=True)
        table.add_column("Token")
        table.add_column("Smart wallets", justify="right")
        table.add_column("Total PnL", justify="right")
        table.add_column("Recent buys", justify="right")
        table.add_column("Recent sells", justify="right")
        table.add_column("Signal")

        for token, wallets in ranked[: self.rows]:
            buys = recent_buys.get(token, 0)
            sells = recent_sells.get(token, 0)
            total_pnl = token_pnl.get(token, 0.0)
            signal = "watch"
            style = ""
            if buys > sells and buys > 0:
                signal = "accumulating"
                style = "green"
            elif sells > buys and sells > 0:
                signal = "distributing"
                style = "red"
            if total_pnl > 0:
                pnl_style = "green"
            else:
                pnl_style = "red"
            table.add_row(token, str(len(wallets)), _num(total_pnl, 0), str(buys), str(sells), signal, style=style)
        return Panel(table, title="Token Consensus", border_style="magenta")

    def _consensus_signals(self, rows: list[dict[str, str]]) -> Panel:
        table = Table(expand=True)
        table.add_column("Symbol")
        table.add_column("Signal")
        table.add_column("Traders", justify="right")
        table.add_column("Long", justify="right")
        table.add_column("Short", justify="right")
        table.add_column("Long ratio", justify="right")
        table.add_column("Short ratio", justify="right")

        for row in rows[: self.rows]:
            signal = row.get("signal", "hold")
            style = "green" if signal == "long" else "red" if signal == "short" else ""
            table.add_row(
                row.get("symbol", ""),
                signal.upper(),
                row.get("trader_count", "0"),
                row.get("long_count", "0"),
                row.get("short_count", "0"),
                _pct(_float(row.get("long_ratio")) * 100),
                _pct(_float(row.get("short_ratio")) * 100),
                style=style,
            )
        if not rows:
            table.add_row("No consensus data", "", "", "", "", "", "", style="dim")
        return Panel(table, title="Consensus Signals", border_style="blue")

    def _recent_alerts(self, alerts: list[dict[str, Any]], trade_rows: list[dict[str, str]]) -> Panel:
        table = Table(expand=True)
        table.add_column("Time")
        table.add_column("Type")
        table.add_column("Wallet")
        table.add_column("Token")
        table.add_column("Detail")

        event_items = [
            {
                "time": row.get("block_time", ""),
                "type": row.get("action", ""),
                "wallet": row.get("wallet", ""),
                "token": row.get("token_symbol", ""),
                "detail": f"SOL {row.get('quote_delta', '')}",
            }
            for row in trade_rows[-self.rows:]
        ]
        alert_items = [
            {
                "time": row.get("collected_at", ""),
                "type": row.get("type", ""),
                "wallet": row.get("wallet", ""),
                "token": row.get("token", ""),
                "detail": f"wallets {row.get('wallet_count') or row.get('token_wallet_count') or ''}",
            }
            for row in alerts[-self.rows:]
        ]
        merged = sorted(event_items + alert_items, key=lambda item: item.get("time", ""), reverse=True)[: self.rows]
        for item in merged:
            kind = item.get("type", "")
            style = "green" if kind == "buy" else "red" if kind == "sell" else "cyan"
            table.add_row(
                item.get("time", "")[11:19] or item.get("time", ""),
                kind,
                _short(item.get("wallet")),
                item.get("token", "")[:12],
                item.get("detail", ""),
                style=style,
            )
        return Panel(table, title="Recent Activity", border_style="blue")

    def _commands(self) -> Panel:
        text = Text()
        if self.collect:
            text.append("Live pipeline: ", style="bold green")
            text.append(f"collect every {self.collect_interval}s, track every {self.track_interval}s\n")
        text.append("Refresh dashboard: ", style="bold")
        text.append("python3 copy_trade_lab.py dashboard --refresh 5\n")
        text.append("Live pipeline: ", style="bold")
        text.append("python3 copy_trade_lab.py dashboard --collect --refresh 10\n")
        text.append("Static view: ", style="bold")
        text.append("python3 copy_trade_lab.py dashboard --static\n")
        text.append("Quick collect: ", style="bold")
        text.append("python3 copy_trade_lab.py okx-sweep --with-positions")
        return Panel(Align.left(text), title="Runbook", border_style="white")


def _read_csv(path: str) -> list[dict[str, str]]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _read_json(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _read_jsonl_tail(path: str, limit: int) -> list[dict[str, Any]]:
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-limit:]


def _float(value: object) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except ValueError:
        return 0.0


def _num(value: object, digits: int) -> str:
    number = _float(value)
    return f"{number:,.{digits}f}"


def _pct(value: float) -> str:
    return f"{value:.1f}%"


def _short(value: object, left: int = 10) -> str:
    text = str(value or "")
    return text[:left] if len(text) <= left else text[:left]


def _wallet_style(win: float, loss: float) -> str:
    if win >= 65 and loss <= 35:
        return "green"
    if loss >= 65:
        return "red"
    return ""


def _net_style(value: float) -> str:
    if value > 0:
        return "green"
    if value < 0:
        return "red"
    return ""


def _now_local() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
