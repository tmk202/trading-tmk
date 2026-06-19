#!/usr/bin/env python3
"""CLI Dashboard — realtime Binance Futures position tracker."""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime

from copy_trade.executor import BinanceFuturesExchange, SYMBOL_MAP as _SYMBOLS

# Rich might not be installed — fallback to plain text
try:
    from rich.console import Console
    from rich.table import Table
    from rich.live import Live
    from rich.text import Text
    RICH = True
except ImportError:
    RICH = False


def _fetch(ex):
    bal = ex.fetch_balance()
    usdt = bal.get("USDT", {})
    total = float(usdt.get("total", 0))
    free = float(usdt.get("free", 0))

    positions = []
    pnl_sum = 0.0
    for sym in sorted(_SYMBOLS.values()):
        try:
            data = ex._signed("GET", "/fapi/v2/positionRisk", {"symbol": ex._symbol(sym)})
            if not data or not isinstance(data, list) or len(data) == 0:
                continue
            p = data[0]
            amt_orig = float(p.get("positionAmt", 0))
            if amt_orig == 0:
                continue
            entry = float(p.get("entryPrice", 0))
            mark = float(p.get("markPrice", 0))
            pnl = float(p.get("unRealizedProfit", 0))
            if pnl == 0 and entry and mark:
                pnl = abs(amt_orig) * ((mark - entry) if amt_orig > 0 else (entry - mark))
            pnl_sum += pnl
            side = "L" if amt_orig > 0 else "S"
            pct = ((mark - entry) if amt_orig > 0 else (entry - mark)) / entry * 100 if entry else 0
            positions.append((side, sym, abs(amt_orig), entry, mark, pnl, pct))
        except Exception:
            pass

    return total, free, total - free, positions, pnl_sum


def render_plain(total, free, margin, positions, pnl_sum):
    now = datetime.now().strftime("%H:%M:%S")
    lines = []
    lines.append(f"\033[2J\033[H")  # clear screen
    lines.append(f"=== Copy Trade CLI {now} ===")
    lines.append(f"Balance: \033[36m${total:,.2f}\033[0m | Free: \033[36m${free:,.2f}\033[0m | Margin: \033[33m${margin:,.2f}\033[0m")
    lines.append(f"PnL: \033[32m${pnl_sum:+,.2f}\033[0m | Active: \033[36m{len(positions)}\033[0m")
    lines.append("")
    lines.append(f"  {'S':1s} {'Symbol':12s} {'Position':>10s} {'Entry':>8s} {'Mark':>8s} {'PnL':>8s} {'%':>6s}")
    lines.append("  " + "-" * 60)
    for side, sym, amt, entry, mark, pnl, pct in positions:
        c = "\033[32m" if pnl >= 0 else "\033[31m"
        lines.append(f"  {side:1s} {sym:12s} {amt:>10.4f} {entry:>8.2f} {mark:>8.2f} {c}${pnl:>+7.2f}\033[0m {pct:>+5.1f}%")
    lines.append("  " + "-" * 60)
    lines.append(f"  TOTAL {len(positions)} positions | PnL \033[32m${pnl_sum:+,.2f}\033[0m")
    print("\n".join(lines))


def render_rich(console, total, free, margin, positions, pnl_sum):
    tg = Table.grid(padding=(0, 2))
    tg.add_column()
    tg.add_column()
    pnl_color = "green" if pnl_sum >= 0 else "red"
    tg.add_row(
        Text(f"Balance: ", style="dim"),
        Text(f"${total:,.2f}", style="cyan"),
        Text("  Free: ", style="dim"),
        Text(f"${free:,.2f}", style="cyan"),
        Text("  Margin: ", style="dim"),
        Text(f"${margin:,.2f}", style="yellow"),
        Text("  PnL: ", style="dim"),
        Text(f"${pnl_sum:+,.2f}", style=pnl_color),
        Text("  Active: ", style="dim"),
        Text(f"{len(positions)}", style="cyan"),
    )

    t = Table(show_header=True, box=None, padding=(0, 1))
    t.add_column("S", style="dim", width=1)
    t.add_column("Symbol", style="cyan", width=12)
    t.add_column("Pos", justify="right", width=10)
    t.add_column("Entry", justify="right", width=8)
    t.add_column("Mark", justify="right", width=8)
    t.add_column("PnL", justify="right", width=8)
    t.add_column("%", justify="right", width=6)

    for side, sym, amt, entry, mark, pnl, pct in positions:
        color = "green" if pnl >= 0 else "red"
        t.add_row(
            side,
            sym,
            f"{amt:.4f}",
            f"{entry:.2f}",
            f"{mark:.2f}",
            f"[{color}]${pnl:+.2f}[/]",
            f"[{color}]{pct:+.1f}%[/]",
        )

    t.add_row("", Text(f"— {len(positions)} positions", style="dim"), "", "", "", Text(f"${pnl_sum:+,.2f}", style=pnl_color), "")

    return [tg, t]


def main():
    ex = BinanceFuturesExchange()
    refresh = int(os.environ.get("CLI_REFRESH", "5"))

    if RICH:
        console = Console()
        with Live(console=console, refresh_per_second=1, screen=True) as live:
            while True:
                data = _fetch(ex)
                live.update(Text.assemble(*render_rich(console, *data)))
                time.sleep(refresh)
    else:
        while True:
            data = _fetch(ex)
            render_plain(*data)
            time.sleep(refresh)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)
