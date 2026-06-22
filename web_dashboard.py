#!/usr/bin/env python3
"""Simple dashboard — match CLI style."""
import csv
import os
import json
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "copy_trade")

BALANCE_CACHE = {"data": None, "time": 0.0}
POSITION_CACHE = {"positions": [], "balance": {}, "time": 0.0}


def _fetch_all():
    now = datetime.now().timestamp()
    cache = POSITION_CACHE
    if now - cache.get("time", 0) < 10:
        return cache.get("positions", []), cache.get("balance", {})
    try:
        from copy_trade.executor import BinanceFuturesExchange, SYMBOL_MAP as _SYMBOLS
        ex = BinanceFuturesExchange()
        bal = ex.fetch_balance()
        usdt = bal.get("USDT", {})
        balance = {
            "total": float(usdt.get("total", 0)),
            "free": float(usdt.get("free", 0)),
        }
        positions = []
        pnl_sum = 0.0
        for sym in sorted(_SYMBOLS.values()):
            try:
                data = ex._signed("GET", "/fapi/v2/positionRisk", {"symbol": ex._symbol(sym)})
                if data and isinstance(data, list) and len(data) > 0:
                    p = data[0]
                    amt = float(p.get("positionAmt", 0))
                    if abs(amt) == 0:
                        continue
                    entry = float(p.get("entryPrice", 0))
                    mark = float(p.get("markPrice", 0))
                    pnl = float(p.get("unRealizedProfit", 0))
                    if pnl == 0 and entry and mark:
                        pnl = abs(amt) * ((mark - entry) if amt > 0 else (entry - mark))
                    pnl_sum += pnl
                    pct = ((mark - entry) if amt > 0 else (entry - mark)) / entry * 100 if entry else 0
                    positions.append({
                        "side": "L" if amt > 0 else "S",
                        "symbol": sym,
                        "amt": f"{abs(amt):.4f}",
                        "entry": f"{entry:.2f}",
                        "mark": f"{mark:.2f}",
                        "pnl_val": pnl,
                        "pnl": f"{pnl:+.2f}",
                        "pct": f"{pct:+.1f}",
                        "pnl_color": "g" if pnl >= 0 else "r",
                    })
            except Exception:
                pass
        balance["pnl"] = pnl_sum
        balance["margin"] = balance["total"] - balance["free"]
        balance["count"] = len(positions)
        cache["positions"] = positions
        cache["balance"] = balance
        cache["time"] = now
        return positions, balance
    except Exception:
        return [], {"total": 0, "free": 0, "margin": 0, "pnl": 0, "count": 0}


HTML = """<!DOCTYPE html>
<html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="10">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📊</text></svg>">
<title>Copy Trade</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:Menlo,Monaco,monospace;background:#0b0f19;color:#e2e8f0;padding:12px;font-size:12px;line-height:1.5}}
.bar{{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:8px;padding:8px 12px;background:#111827;border-radius:8px}}
.bar span{{color:#38bdf8}}
.g{{color:#22c55e}}.r{{color:#ef4444}}.dim{{color:#64748b}}
table{{width:100%;border-collapse:collapse}}
th{{text-align:left;padding:4px 8px;color:#64748b;font-weight:400;border-bottom:1px solid #1e293b}}
td{{padding:3px 8px;border-bottom:1px solid #0f172a}}
</style></head><body>

<div class="bar">
Balance: <span>${total:,.2f}</span>
Free: <span>${free:,.2f}</span>
Margin: <span>${margin:,.2f}</span>
Active: <span>{count}</span>
PnL: <span class="{pnl_color}">${pnl:+,.2f}</span>
<span class="dim">{time}</span>
</div>

<table>
<tr><th>S</th><th>Symbol</th><th>Position</th><th>Entry</th><th>Mark</th><th>PnL</th><th>%</th></tr>
{rows}
</table>
<div class="dim" style="margin-top:8px">TP 5% | SL 3% | Refresh 10s | {time}</div>

</body></html>"""


def _float(v, default=0.0):
    try: return float(str(v).replace(",", "").replace("$", ""))
    except: return default


def make_app(environ, start_response):
    try:
        positions, bal = _fetch_all()
        now = datetime.now().strftime("%H:%M:%S")

        rows = ""
        for p in positions:
            c = p["pnl_color"]
            rows += (
                f"<tr>"
                f"<td class='dim'>{p['side']}</td>"
                f"<td>{p['symbol']}</td>"
                f"<td class='dim'>{p['amt']}</td>"
                f"<td class='dim'>{p['entry']}</td>"
                f"<td class='dim'>{p['mark']}</td>"
                f"<td class='{c}'>${p['pnl']}</td>"
                f"<td class='{c}'>{p['pct']}%</td>"
                f"</tr>"
            )

        html = HTML.format(
            total=bal.get("total", 0),
            free=bal.get("free", 0),
            margin=bal.get("margin", 0),
            count=bal.get("count", 0),
            pnl=bal.get("pnl", 0),
            pnl_color="g" if bal.get("pnl", 0) >= 0 else "r",
            rows=rows or "<tr><td colspan=7 class='dim'>No positions</td></tr>",
            time=now,
        )
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [html.encode()]
    except Exception as e:
        start_response("500 ERROR", [("Content-Type", "text/plain")])
        return [f"Error: {e}".encode()]


if __name__ == "__main__":
    from wsgiref.simple_server import make_server
    port = int(os.environ.get("PORT", 8080))
    httpd = make_server("0.0.0.0", port, make_app)
    print(f"Dashboard -> http://0.0.0.0:{port}")
    httpd.serve_forever()
