#!/usr/bin/env python3
"""Simple dashboard for Copy Trade Bot — beginner-friendly."""
import csv
import os
import json
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "copy_trade")

BALANCE_CACHE = {"data": None, "time": 0.0}
POSITION_PNL_CACHE = {"data": {}, "time": 0.0}


def fetch_balance():
    now = datetime.now().timestamp()
    if now - BALANCE_CACHE.get("time", 0) < 10:
        return BALANCE_CACHE.get("data", {})
    try:
        from copy_trade.executor import BinanceFuturesExchange
        ex = BinanceFuturesExchange()
        data = ex.fetch_balance()
        BALANCE_CACHE["data"] = data
        BALANCE_CACHE["time"] = now
        return data
    except Exception:
        return {}


def fetch_unrealized_pnl(symbol):
    """Return (pnl_usd, notional, pnl_pct) or (None, None, None)."""
    now = datetime.now().timestamp()
    cache = POSITION_PNL_CACHE
    if now - cache.get("time", 0) < 10:
        item = cache["data"].get(symbol)
        if item:
            return item.get("pnl"), item.get("notional"), item.get("pnl_pct")
        return None, None, None
    try:
        from copy_trade.executor import BinanceFuturesExchange
        ex = BinanceFuturesExchange()
        result = {}
        syms = set()
        for name in os.listdir(DATA_DIR):
            if name == "trade_history.csv":
                with open(os.path.join(DATA_DIR, name)) as f:
                    for r in csv.DictReader(f):
                        if r.get("action") == "open" and r.get("dry_run") == "False":
                            s = r.get("symbol", "")
                            if "/" in s:
                                syms.add(s)
        for sym in syms:
            data = ex._signed("GET", "/fapi/v2/positionRisk", {"symbol": ex._symbol(sym)})
            if data and isinstance(data, list) and len(data) > 0:
                p = data[0]
                amt = abs(float(p.get("positionAmt", 0)))
                entry = float(p.get("entryPrice", 0))
                mark = float(p.get("markPrice", 0))
                pnl = float(p.get("unRealizedProfit", 0))
                notional = amt * mark if amt and mark else 0
                is_long = float(p.get("positionAmt", 0)) > 0
                pct_price = ((mark - entry) if is_long else (entry - mark)) / entry * 100 if entry else 0
                result[sym] = {"pnl": pnl, "notional": notional, "pnl_pct": round(pct_price, 1)}
        cache["data"] = result
        cache["time"] = now
        item = result.get(symbol)
        if item:
            return item.get("pnl"), item.get("notional"), item.get("pnl_pct")
    except Exception:
        pass
    return None, None, None


HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Copy Trade Bot</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#0b0f19;color:#e2e8f0;padding:16px;max-width:900px;margin:auto}}
h1{{font-size:20px;color:#38bdf8;margin-bottom:16px}}
.card{{background:#111827;border:1px solid #1e293b;border-radius:10px;padding:14px;margin-bottom:12px}}
.stats{{display:flex;gap:10px;flex-wrap:wrap}}
.stat{{flex:1;min-width:100px;text-align:center;padding:10px;background:#0f172a;border-radius:8px}}
.stat-value{{font-size:22px;font-weight:700}}
.stat-label{{font-size:11px;color:#64748b;margin-top:2px}}
.green{{color:#22c55e}}.red{{color:#ef4444}}.blue{{color:#38bdf8}}.amber{{color:#f59e0b}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;padding:6px 8px;color:#64748b;font-weight:600;border-bottom:1px solid #1e293b;font-size:11px}}
td{{padding:6px 8px;border-bottom:1px solid #0f172a}}
.side-buy{{color:#22c55e}}.side-sell{{color:#ef4444}}
.pnl{{font-weight:600}}
.tier-hot{{background:#422006;color:#f59e0b;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:700}}
.tier-warm{{background:#1e3a2f;color:#22c55e;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:700}}
.dry{{color:#64748b;font-style:italic}}
.empty{{text-align:center;color:#475569;padding:20px}}
small{{color:#64748b;font-size:11px}}
hr{{border:0;border-top:1px solid #1e293b;margin:8px 0}}
</style>
<meta http-equiv="refresh" content="30">
</head>
<body>

<h1>Copy Trade Bot</h1>

<div class="stats">
<div class="stat">
  <div class="stat-value blue">{cycles}</div>
  <div class="stat-label">Cycles</div>
</div>
<div class="stat">
  <div class="stat-value {pnl_color}">{total_pnl}</div>
  <div class="stat-label">Est PnL</div>
</div>
<div class="stat">
  <div class="stat-value">{positions}</div>
  <div class="stat-label">Active</div>
</div>
<div class="stat">
  <div class="stat-value">{hot_count}+{warm_count}</div>
  <div class="stat-label">Hot / Warm</div>
</div>
<div class="stat">
  <div class="stat-value {err_color}">{errors}</div>
  <div class="stat-label">Errors</div>
</div>
</div>

<div class="card">
<h2 style="font-size:14px;color:#38bdf8;margin-bottom:8px">Hot Wallets <small>(copy x2.0)</small></h2>
{hot_table}
<hr>
<h2 style="font-size:14px;color:#22c55e;margin-bottom:8px">Warm Wallets <small>(copy x1.0)</small></h2>
{warm_table}
</div>

<div class="card">
<h2 style="font-size:14px;color:#38bdf8;margin-bottom:8px">Recent Trades</h2>
{trade_table}
</div>

<div class="card">
<h2 style="font-size:14px;color:#38bdf8;margin-bottom:8px">Balance</h2>
{balance_str}
</div>

<div class="card">
<small>Last cycle: {last_time} &bull; Auto-refresh 30s &bull; {mode} @ {interval}m &bull; {dry_label}</small>
</div>

</body></html>"""


def read_csv(name):
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_json(name):
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def _float(v, default=0.0):
    try:
        return float(str(v).replace(",", "").replace("$", ""))
    except (ValueError, TypeError):
        return default


def make_app(environ, start_response):
    try:
        trades = read_csv("trade_history.csv")
        promising = read_csv("promising_wallets.csv")

        # Populate position cache upfront
        fetch_unrealized_pnl("")

        # -- Stats --
        pos_count = 0
        pnl_data = POSITION_PNL_CACHE.get("data", {})
        if pnl_data:
            pos_count = sum(1 for v in pnl_data.values() if isinstance(v, dict) and v.get("notional", 0) > 0)
        closed = [t for t in trades if "close" in (t.get("action") or "")]
        total_pnl = sum(display_pnl for display_pnl in (_float(t.get("pnl") or 0) for t in closed))

        hot = [r for r in promising if r.get("tier") == "hot"]
        warm = [r for r in promising if r.get("tier") == "warm"]

        # -- Hot table --
        hot_rows = ""
        for r in hot[:8]:
            hot_rows += (
                f"<tr>"
                f"<td><span class='tier-hot'>HOT</span></td>"
                f"<td>{r.get('wallet','')[:12]}...</td>"
                f"<td style='color:#38bdf8'>{_float(r.get('pnl_30d')):,.0f}</td>"
                f"<td>{_float(r.get('roi_pct')):.0f}%</td>"
                f"<td>{r.get('win_rate_pct', '-')}%</td>"
                f"<td>${r.get('position_size_usd', '-')}</td>"
                f"</tr>"
            )
        if not hot_rows:
            hot_rows = "<tr><td colspan=6 class='empty'>No hot wallets yet</td></tr>"

        # -- Warm table --
        warm_rows = ""
        for r in warm[:8]:
            warm_rows += (
                f"<tr>"
                f"<td><span class='tier-warm'>WARM</span></td>"
                f"<td>{r.get('wallet','')[:12]}...</td>"
                f"<td style='color:#22c55e'>{_float(r.get('pnl_30d')):,.0f}</td>"
                f"<td>{_float(r.get('roi_pct')):.0f}%</td>"
                f"<td>{r.get('win_rate_pct', '-')}%</td>"
                f"<td>${r.get('position_size_usd', '-')}</td>"
                f"</tr>"
            )
        if not warm_rows:
            warm_rows = "<tr><td colspan=6 class='empty'>No warm wallets yet</td></tr>"

        # -- Trade table --
        recent = trades[-20:][::-1]
        trade_rows = ""
        seen_symbols = set()
        for t in recent:
            action = t.get("action", "")
            symbol = t.get("symbol", "") or t.get("source_symbol", "")
            is_live = t.get("dry_run") != "True" and action == "open"
            # Dedup: for LIVE trades, show only latest per symbol
            if is_live:
                if symbol in seen_symbols:
                    continue
                seen_symbols.add(symbol)
            is_dry = t.get("dry_run") == "True"
            is_live_open = (action == "open" and not is_dry and not _float(t.get("pnl")))
            side = t.get("side", "")
            size = t.get("size_usd", "")
            raw_pnl = _float(t.get("pnl"))
            ts = t.get("timestamp", "")[:19].replace("T", " ")
            label = "DR" if is_dry else "LIVE"
            side_class = f"side-{side}" if side in ("buy", "sell") else ""
            pnl_str = ""
            pnl_class = ""
            display_size = f"${_float(size):.0f}"
            display_pnl = raw_pnl
            if is_live_open:
                live_pnl, live_notional, live_pct = fetch_unrealized_pnl(symbol)
                if live_pnl is not None:
                    display_pnl = live_pnl
                    if live_notional:
                        display_size = f"${live_notional:.0f}"
            if display_pnl:
                pct_str = f" ({live_pct:+.1f}%)" if is_live_open and live_pct is not None else ""
                pnl_str = f"{display_pnl:+.2f}{pct_str}"
                pnl_class = "green" if display_pnl > 0 else "red"
            trade_rows += (
                f"<tr>"
                f"<td class='dry'>{ts[5:16] if ts else ''}</td>"
                f"<td class='dry'>{label}</td>"
                f"<td class='{side_class}'>{side.upper():4}</td>"
                f"<td>{symbol[:14]}</td>"
                f"<td>{display_size}</td>"
                f"<td class='{pnl_class}'>{pnl_str}</td>"
                f"</tr>"
            )
        if not trade_rows:
            trade_rows = "<tr><td colspan=6 class='empty'>No trades yet — waiting...</td></tr>"

        pnl_color = "green" if total_pnl >= 0 else "red"
        err_color = "red" if len([t for t in trades if t.get("action", "").startswith("close_dry") and _float(t.get("pnl") or 0) < 0]) > 0 else "green"

        state = read_json("hyperliquid_tracker_state.json")
        last_time = (datetime.now()).strftime("%H:%M:%S")

        # Balance
        bal = fetch_balance()
        usdt = bal.get("USDT", {})
        bal_total = _float(usdt.get("total"))
        bal_free = _float(usdt.get("free"))
        bal_margin = bal_total - bal_free
        bal_color = "green" if bal_total > 5000 else "red"
        balance_str = (
            f'<div class="stats">'
            f'<div class="stat"><div class="stat-value {bal_color}">${bal_total:.0f}</div><div class="stat-label">Total</div></div>'
            f'<div class="stat"><div class="stat-value blue">${bal_free:.0f}</div><div class="stat-label">Available</div></div>'
            f'<div class="stat"><div class="stat-value amber">${bal_margin:.0f}</div><div class="stat-label">In Positions</div></div>'
            f'<div class="stat"><div class="stat-value green">${bal_total - 5000:+.2f}</div><div class="stat-label">Unrealized PnL</div></div>'
            f'</div>'
        ) if bal_total > 0 else '<p class="empty">Logging in...</p>'

        html = HTML.format(
            cycles=len(set(t.get("timestamp", "")[:10] for t in trades)),
            total_pnl=f"{total_pnl:+.0f}" if total_pnl else "—",
            pnl_color=pnl_color,
            positions=pos_count,
            hot_count=len(hot),
            warm_count=len(warm),
            errors=sum(1 for t in trades if "error" in (t.get("action") or "").lower()),
            err_color=err_color,
            hot_table=f"<table><thead><tr><th></th><th>Wallet</th><th>PnL 30d</th><th>ROI</th><th>WR</th><th>Size</th></tr></thead>{hot_rows}</table>",
            warm_table=f"<table><thead><tr><th></th><th>Wallet</th><th>PnL 30d</th><th>ROI</th><th>WR</th><th>Size</th></tr></thead>{warm_rows}</table>",
            trade_table=f"<table><thead><tr><th>Time</th><th></th><th>Side</th><th>Symbol</th><th>Size</th><th>PnL</th></tr></thead>{trade_rows}</table>",
            balance_str=balance_str,
            last_time=last_time,
            mode="HL",
            interval="5",
            dry_label="LIVE" if pos_count > 0 else "IDLE",
        )

        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [html.encode()]
    except Exception as e:
        start_response("500 ERROR", [("Content-Type", "text/plain")])
        return [f"Dashboard error: {e}".encode()]


if __name__ == "__main__":
    from wsgiref.simple_server import make_server
    port = int(os.environ.get("PORT", 8080))
    httpd = make_server("0.0.0.0", port, make_app)
    print(f"Dashboard -> http://0.0.0.0:{port}")
    httpd.serve_forever()
