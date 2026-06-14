#!/usr/bin/env python3
import csv
import os
import subprocess
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "copy_trade")

HTML = """<!DOCTYPE html>
<html lang="vi">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trading TMK - Dashboard</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:system-ui,-apple-system,sans-serif;background:#0d1117;color:#c9d1d9;padding:20px;max-width:1200px;margin:auto}}
h1{{color:#58a6ff;margin-bottom:20px;font-size:24px}}
h2{{color:#8b949e;font-size:16px;margin:20px 0 10px;border-bottom:1px solid #21262d;padding-bottom:5px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:16px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}}
.stat{{text-align:center;padding:12px;background:#0d1117;border-radius:6px}}
.stat-value{{font-size:28px;font-weight:700;color:#f0f6fc}}
.stat-label{{font-size:12px;color:#8b949e;margin-top:4px}}
table{{width:100%;border-collapse:collapse;margin-top:8px;font-size:13px}}
th,td{{text-align:left;padding:8px 12px;border-bottom:1px solid #21262d}}
th{{color:#8b949e;font-weight:600}}
.buy{{color:#3fb950}}
.sell{{color:#f85149}}
.hold{{color:#8b949e}}
.error{{color:#f85149}}
.ok{{color:#3fb950}}
.warn{{color:#d29922}}
</style></head>
<body>
<h1>🤖 Trading TMK — Copy Trade Bot</h1>
<div class="grid">
<div class="stat"><div class="stat-value">{cycles}</div><div class="stat-label">Cycles</div></div>
<div class="stat"><div class="stat-value">{bin_signals}</div><div class="stat-label">Binance Signals</div></div>
<div class="stat"><div class="stat-value">{hl_signals}</div><div class="stat-label">Hyperliquid Signals</div></div>
<div class="stat"><div class="stat-value">{sol_signals}</div><div class="stat-label">Solana Signals</div></div>
<div class="stat"><div class="stat-value">{errors}</div><div class="stat-label">Errors</div></div>
<div class="stat"><div class="stat-value">{wallets}</div><div class="stat-label">Trusted Wallets</div></div>
</div>

<h2>Trade History</h2>
<div class="card">{trade_table}</div>

<h2>Active Positions</h2>
<div class="card">{positions_table}</div>

<h2>Last Cycle</h2>
<div class="card"><pre style="font-size:12px;color:#8b949e">{last_cycle}</pre></div>
</body></html>"""

def csv_to_table(rows, limit=20):
    if not rows:
        return "<p style='color:#8b949e'>No data</p>"
    headers = list(rows[0].keys())[:8]
    thead = "".join(f"<th>{h}</th>" for h in headers)
    tbody = ""
    for r in rows[:limit]:
        cells = "".join(f"<td>{r.get(h, '')}</td>" for h in headers)
        tbody += f"<tr>{cells}</tr>"
    return f"<table><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table>"

def read_csv(name):
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))

def make_app(environ, start_response):
    try:
        trades = read_csv("trade_history.csv")[::-1]
        positions = read_csv("wallet_selection.csv")
        cycle_log = ""
        log_path = os.path.join(DATA_DIR, "..", "..", "cycle.log")
        if os.path.exists(log_path):
            with open(log_path) as f:
                cycle_log = f.read()[:500]

        bin_sigs = sum(1 for t in trades if "binance" in t.get("source","").lower())
        hl_sigs = sum(1 for t in trades if "hyperliquid" in t.get("source","").lower())
        sol_sigs = sum(1 for t in trades if t.get("source","") in ("wallet_copy","solana"))

        active_bin = len([t for t in trades if "open" in t.get("action","") and "binance" in t.get("source","").lower() and "close" not in t.get("action","")])

        trade_table = csv_to_table(trades)
        pos_table = csv_to_table(positions, 10)

        html = HTML.format(
            cycles=len(set(t.get("timestamp","")[:10] for t in trades)) or "?",
            bin_signals=bin_sigs,
            hl_signals=hl_sigs,
            sol_signals=sol_sigs,
            errors=sum(1 for t in trades if "error" in t.get("action","")),
            wallets=len(positions),
            trade_table=trade_table,
            positions_table=pos_table,
            last_cycle=cycle_log if cycle_log else "Waiting for first cycle...",
        )

        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [html.encode()]
    except Exception as e:
        start_response("500 ERROR", [("Content-Type", "text/plain")])
        return [f"Dashboard error: {e}".encode()]

if __name__ == "__main__":
    from wsgiref.simple_server import make_server
    httpd = make_server("0.0.0.0", 8080, make_app)
    print("Dashboard at http://0.0.0.0:8080")
    httpd.serve_forever()
