#!/usr/bin/env python3
"""Test cTrader API connection. Run this first to verify credentials work.

Usage:
  CTRADER_CLIENT_ID=xxx CTRADER_CLIENT_SECRET=yyy python3 _test_ctrader.py
"""
import json
import os
import sys
import requests
from dotenv import load_dotenv
load_dotenv()

CLIENT_ID = os.getenv("CTRADER_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("CTRADER_CLIENT_SECRET", "")
ACCOUNT_ID = os.getenv("CTRADER_ACCOUNT_ID", "")
DEMO = os.getenv("CTRADER_DEMO", "true").lower() == "true"

API_BASE = "https://demo.ctraderapi.com" if DEMO else "https://openapi.ctrader.com"

if not CLIENT_ID or not CLIENT_SECRET:
    print("ERROR: Set CTRADER_CLIENT_ID and CTRADER_CLIENT_SECRET env vars")
    print("Example: CTRADER_CLIENT_ID=xxx CTRADER_CLIENT_SECRET=yyy python3 _test_ctrader.py")
    sys.exit(1)

session = requests.Session()
print(f"Testing connection to: {API_BASE}")
print(f"Demo mode: {DEMO}")

# Step 1: Get token
print("\n[1] Requesting OAuth token...")
try:
    resp = session.post(
        f"{API_BASE}/apps/token",
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope": "accounts+trading",
        },
        timeout=30,
    )
    print(f"    Status: {resp.status_code}")
    
    if resp.status_code == 200:
        data = resp.json()
        if "access_token" in data:
            token = data["access_token"]
            print(f"    Token OK (expires in {data.get('expires_in', '?')}s)")
            session.headers.update({"Authorization": f"Bearer {token}"})
        else:
            print(f"    Unexpected response: {json.dumps(data, indent=2)}")
            sys.exit(1)
    else:
        print(f"    FAILED: {resp.text[:500]}")
        print("\n    Possible issues:")
        print("    1. Client ID / Secret wrong")
        print("    2. App not authorized yet - go to:")
        print(f"       https://openapi.ctrader.com/apps/auth?client_id={CLIENT_ID}&redirect_uri=http://localhost:3000&scope=accounts+trading")
        print("       Login with your cTrader ID and authorize the app.")
        sys.exit(1)
except Exception as e:
    print(f"    ERROR: {e}")
    sys.exit(1)

# Step 2: List accounts
print("\n[2] Listing accounts...")
try:
    resp = session.get(f"{API_BASE}/v1/accounts", timeout=30)
    print(f"    Status: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        accounts = data.get("accounts", [])
        print(f"    Found {len(accounts)} account(s):")
        for acc in accounts:
            aid = acc.get("accountId", "?")
            name = acc.get("accountName", "")
            atype = acc.get("accountType", "")
            currency = acc.get("depositCurrency", "")
            balance = acc.get("balance", "?")
            print(f"      Account {aid}: {name} | type={atype} currency={currency} balance={balance}")
            if not ACCOUNT_ID or ACCOUNT_ID == str(aid):
                ACCOUNT_ID = str(aid)
    else:
        print(f"    RESPONSE: {resp.text[:500]}")
        sys.exit(1)
except Exception as e:
    print(f"    ERROR: {e}")
    sys.exit(1)

if not ACCOUNT_ID:
    print("\nERROR: No account found. Add CTRADER_ACCOUNT_ID to your .env with one of the IDs above")
    sys.exit(1)

# Step 3: Get account info
print(f"\n[3] Checking account {ACCOUNT_ID}...")
try:
    resp = session.get(f"{API_BASE}/v2/accounts/{ACCOUNT_ID}", timeout=30)
    print(f"    Status: {resp.status_code}")
    if resp.status_code == 200:
        acc = resp.json().get("account", resp.json())
        print(f"    Balance:  {acc.get('balance')}")
        print(f"    Equity:   {acc.get('equity')}")
        print(f"    Margin:   {acc.get('usedMargin')}")
        print(f"    Free:     {acc.get('freeMargin')}")
    else:
        print(f"    RESPONSE: {resp.text[:300]}")
except Exception as e:
    print(f"    ERROR: {e}")
    sys.exit(1)

# Step 4: Get symbols (just EUR/USD as test)
print("\n[4] Checking EUR/USD symbol...")
try:
    resp = session.get(f"{API_BASE}/v2/accounts/{ACCOUNT_ID}/symbols", timeout=30)
    if resp.status_code == 200:
        symbols = resp.json().get("symbols", [])
        eurusd = [s for s in symbols if s.get("symbolName", "").upper() == "EURUSD"]
        if eurusd:
            s = eurusd[0]
            print(f"    EUR/USD found! symbolId={s.get('symbolId')}")
            print(f"    Bid={s.get('quote',{}).get('bid')} Ask={s.get('quote',{}).get('ask')}")
        else:
            print(f"    EUR/USD not found in {len(symbols)} symbols. Looking...")
            for s in symbols[:5]:
                print(f"      {s.get('symbolId')}: {s.get('symbolName')}")
    else:
        print(f"    RESPONSE: {resp.text[:300]}")
except Exception as e:
    print(f"    ERROR: {e}")

print(f"\n{'='*50}")
print("ALL CHECKS PASSED")
print(f"Add to .env: CTRADER_ACCOUNT_ID={ACCOUNT_ID}")
print(f"Then run: python3 main_copy_trade.py --mode forex --no-dry-run --once")
