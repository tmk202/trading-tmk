#!/usr/bin/env python3
"""
Copy Trade Lab — trader positioning research.

Usage:
  python3 copy_trade_lab.py collect --provider bitget
  python3 copy_trade_lab.py collect --provider csv --traders-csv sample_traders.csv --positions-csv sample_positions.csv
  python3 copy_trade_lab.py consensus --positions-csv sample_positions.csv --traders-csv sample_traders.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import time

from copy_trade.analyzer import build_consensus, select_traders
from copy_trade.browser_scraper import (
    BrowserScrapeError,
    ChromeDumpBrowser,
    discover_page,
    discover_page_with_assets,
    fetch_static_page,
    parse_generic_traders,
    save_browser_artifacts,
)
from copy_trade.dashboard import CopyTradeDashboard
from copy_trade.models import PositionSnapshot
from copy_trade.models import TraderSnapshot
from copy_trade.models import utc_now_iso
from copy_trade.providers import CsvProvider, ProviderError, make_provider
from copy_trade.hyperliquid_tracker import (
    DEXLY_LEADERBOARD_URL,
    HyperliquidTracker,
    load_hyperliquid_state,
    load_hyperliquid_wallets,
    save_hyperliquid_state,
)
from copy_trade.storage import CopyTradeStore

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "copy_trade")
ARTIFACT_DIR = os.path.join(DATA_DIR, "browser_artifacts")


def cmd_collect(args: argparse.Namespace) -> int:
    provider = make_provider(
        args.provider,
        args.traders_csv,
        args.positions_csv,
        category=getattr(args, "category", "CRYPTO"),
        time_period=getattr(args, "time_period", "MONTH"),
        order_by=getattr(args, "order_by", "PNL"),
        size_threshold=getattr(args, "size_threshold", 1),
        wallets_csv=getattr(args, "wallets_csv", None),
        okx_url=getattr(args, "okx_url", None),
        okx_chain_id=getattr(args, "okx_chain_id", "501"),
        okx_rank_by=getattr(args, "okx_rank_by", "pnl"),
        okx_period=getattr(args, "okx_period", "30d"),
    )
    store = CopyTradeStore(args.data_dir)

    try:
        traders = provider.fetch_traders(limit=args.limit)
    except ProviderError as exc:
        print(f"Provider blocked: {exc}")
        return 2

    positions = []
    if isinstance(provider, CsvProvider):
        positions = provider.fetch_all_positions()
    elif args.with_positions:
        for trader in traders:
            try:
                positions.extend(provider.fetch_positions(trader.trader_id))
            except ProviderError as exc:
                print(f"Position fetch skipped: {exc}")

    traders_path = store.append_csv("trader_daily_stats.csv", traders)
    store.append_jsonl("trader_daily_stats.jsonl", traders)
    print(f"Saved traders: {len(traders)} -> {traders_path}")

    if positions:
        positions_path = store.append_csv("trader_positions.csv", positions)
        store.append_jsonl("trader_positions.jsonl", positions)
        print(f"Saved positions: {len(positions)} -> {positions_path}")
    else:
        print("Saved positions: 0")

    return 0


def cmd_okx_sweep(args: argparse.Namespace) -> int:
    rank_modes = _split_csv_arg(args.rank_by)
    periods = _split_csv_arg(args.periods)
    chain_ids = _split_csv_arg(getattr(args, "chain_ids", args.chain_id))
    seen = set()
    traders: list[TraderSnapshot] = []
    positions: list[PositionSnapshot] = []

    for chain_id in chain_ids:
        for period in periods:
            for rank_by in rank_modes:
                provider = make_provider(
                    "okx_web3",
                    okx_url=args.okx_url,
                    okx_chain_id=chain_id,
                    okx_rank_by=rank_by,
                    okx_period=period,
                )
                try:
                    batch = provider.fetch_traders(limit=args.per_rank_limit)
                except ProviderError as exc:
                    print(f"OKX sweep skipped chain={chain_id} rank_by={rank_by} period={period}: {exc}")
                    continue
                added = 0
                for trader in batch:
                    if trader.trader_id in seen:
                        continue
                    seen.add(trader.trader_id)
                    trader.raw["chain_id"] = chain_id
                    traders.append(trader)
                    added += 1
                    if args.with_positions:
                        positions.extend(provider.fetch_positions(trader.trader_id))
                    if len(traders) >= args.max_wallets:
                        break
                print(f"sweep chain={chain_id} rank_by={rank_by:<8} period={period:<3} fetched={len(batch):<4} added={added:<4} total={len(traders)}")
                if len(traders) >= args.max_wallets:
                    break
            if len(traders) >= args.max_wallets:
                break
        if len(traders) >= args.max_wallets:
            break

    store = CopyTradeStore(args.data_dir)
    traders_path = store.append_csv("trader_daily_stats.csv", traders)
    store.append_jsonl("trader_daily_stats.jsonl", traders)
    print(f"\nSaved traders: {len(traders)} -> {traders_path}")

    if positions:
        positions_path = store.append_csv("trader_positions.csv", positions)
        store.append_jsonl("trader_positions.jsonl", positions)
        print(f"Saved positions: {len(positions)} -> {positions_path}")
    else:
        print("Saved positions: 0")
    return 0 if traders else 1


def cmd_consensus(args: argparse.Namespace) -> int:
    provider = CsvProvider(traders_csv=args.traders_csv, positions_csv=args.positions_csv)
    traders = provider.fetch_traders(limit=args.limit) if args.traders_csv else []
    positions = provider.fetch_all_positions()

    selected = select_traders(
        traders,
        limit=args.top,
        max_drawdown=args.max_drawdown,
        min_win_rate=args.min_win_rate,
        min_copy_days=args.min_copy_days,
    ) if traders else None

    signals = build_consensus(positions, selected_traders=selected, threshold=args.threshold)
    if not signals:
        print("No consensus signals. Need positions CSV with trader_id,symbol,side.")
        return 1

    store = CopyTradeStore(args.data_dir)
    path = store.append_csv("consensus_signals.csv", signals)
    print(f"Saved consensus: {len(signals)} -> {path}\n")

    print("=== CONSENSUS ===")
    for signal in signals[: args.rows]:
        print(
            f"{signal.symbol:<12} signal={signal.signal:<5} "
            f"traders={signal.trader_count:<3} "
            f"long={signal.long_count} ({signal.long_ratio:.0%}) "
            f"short={signal.short_count} ({signal.short_ratio:.0%}) "
            f"weights L/S={signal.long_weight:.2f}/{signal.short_weight:.2f}"
        )
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    provider = CsvProvider(traders_csv=args.traders_csv)
    traders = provider.fetch_traders(limit=args.limit)
    selected = select_traders(
        traders,
        limit=args.top,
        max_drawdown=args.max_drawdown,
        min_win_rate=args.min_win_rate,
        min_copy_days=args.min_copy_days,
    )

    print("=== SELECTED TRADERS ===")
    if not selected:
        print("No traders passed filters.")
        return 1
    for trader in selected:
        print(
            f"{trader.rank or '-':>3} {trader.trader_id:<20} "
            f"roi30={_fmt(trader.roi_30d):>8} "
            f"dd={_fmt(trader.drawdown):>8} "
            f"win={_fmt(trader.win_rate):>8} "
            f"followers={trader.followers if trader.followers is not None else '-'} "
            f"{trader.nickname}"
        )
    return 0


def cmd_watchlist(args: argparse.Namespace) -> int:
    if not os.path.exists(args.traders_csv):
        print(f"Missing traders CSV: {args.traders_csv}")
        return 2

    latest_by_wallet = {}
    with open(args.traders_csv, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if args.platform and row.get("platform") != args.platform:
                continue
            wallet = row.get("trader_id") or ""
            if not wallet:
                continue
            latest_by_wallet[wallet] = row

    candidates = []
    for row in latest_by_wallet.values():
        pnl = _to_float(row.get("pnl_30d"))
        roi = _to_float(row.get("roi_30d"))
        win_rate = _to_float(row.get("win_rate"))
        tx = _to_float(row.get("total_trades"))
        volume = _to_float(row.get("aum"))
        if pnl is None or pnl < args.min_pnl:
            continue
        if roi is None or roi < args.min_roi:
            continue
        if win_rate is None or win_rate < args.min_win_rate:
            continue
        if tx is None or tx < args.min_trades:
            continue

        raw = _json_loads(row.get("raw"))
        top_tokens = [
            str(token.get("tokenSymbol") or token.get("tokenAddress") or "").strip()
            for token in raw.get("topTokens", [])
            if isinstance(token, dict)
        ]
        score = _wallet_score(pnl=pnl, roi=roi, win_rate=win_rate, tx=tx, volume=volume)
        candidates.append({
            "collected_at": row.get("collected_at", ""),
            "platform": row.get("platform", ""),
            "wallet": row.get("trader_id", ""),
            "nickname": row.get("nickname", ""),
            "rank": row.get("rank", ""),
            "score": round(score, 4),
            "pnl_30d": pnl,
            "roi_30d": roi,
            "win_rate": win_rate,
            "tx": int(tx),
            "volume": volume,
            "top_tokens": "|".join(token for token in top_tokens if token),
            "profile_url": raw.get("profile_url", ""),
        })

    candidates.sort(key=lambda item: item["score"], reverse=True)
    selected = candidates[: args.top]

    os.makedirs(args.data_dir, exist_ok=True)
    out_path = args.output or os.path.join(args.data_dir, "wallet_watchlist.csv")
    fieldnames = [
        "collected_at", "platform", "wallet", "nickname", "rank", "score",
        "pnl_30d", "roi_30d", "win_rate", "tx", "volume", "top_tokens", "profile_url",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(selected)

    print(f"Saved watchlist: {len(selected)} -> {out_path}\n")
    print("=== WALLET WATCHLIST ===")
    if not selected:
        print("No wallets passed filters.")
        return 1
    for item in selected[: args.rows]:
        print(
            f"{item['score']:>7.2f} {item['wallet'][:10]:<10} "
            f"pnl={item['pnl_30d']:>9.2f} roi={item['roi_30d']:>8.2f} "
            f"win={item['win_rate']:>6.2f} tx={item['tx']:<5} "
            f"{item['nickname']} tokens={item['top_tokens'][:80]}"
        )
    return 0


def cmd_wallet_performance(args: argparse.Namespace) -> int:
    if not os.path.exists(args.traders_csv):
        print(f"Missing traders CSV: {args.traders_csv}")
        return 2

    latest_by_wallet = {}
    with open(args.traders_csv, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if args.platform and row.get("platform") != args.platform:
                continue
            wallet = row.get("trader_id") or ""
            if not wallet:
                continue
            latest_by_wallet[wallet] = row

    rows = []
    for row in latest_by_wallet.values():
        raw = _json_loads(row.get("raw"))
        pnl = _to_float(row.get("pnl_30d")) or 0.0
        roi = _to_float(row.get("roi_30d")) or 0.0
        win_rate = _to_float(row.get("win_rate")) or 0.0
        tx = _to_float(row.get("total_trades")) or 0.0
        if tx < args.min_trades or pnl < args.min_pnl or win_rate < args.min_win_rate:
            continue
        wins = round(tx * win_rate / 100.0, 2)
        losses = round(max(tx - wins, 0), 2)
        top_tokens = raw.get("topTokens") if isinstance(raw.get("topTokens"), list) else []
        token_wins = sum(1 for token in top_tokens if (_to_float(token.get("pnl")) or 0) > 0)
        token_losses = sum(1 for token in top_tokens if (_to_float(token.get("pnl")) or 0) < 0)
        pnl_stats = _pnl_history_stats(raw.get("pnlHistory") or [])
        score = _wallet_score(
            pnl=pnl,
            roi=roi,
            win_rate=win_rate,
            tx=tx,
            volume=_to_float(row.get("aum")),
        )
        rows.append({
            "platform": row.get("platform", ""),
            "wallet": row.get("trader_id", ""),
            "nickname": row.get("nickname", ""),
            "rank": row.get("rank", ""),
            "score": round(score, 4),
            "pnl": round(pnl, 6),
            "roi_pct": round(roi, 6),
            "win_rate_pct": round(win_rate, 4),
            "tx": int(tx),
            "estimated_wins": wins,
            "estimated_losses": losses,
            "estimated_loss_rate_pct": round(100 - win_rate, 4),
            "top_token_wins": token_wins,
            "top_token_losses": token_losses,
            "pnl_history_change": pnl_stats["change"],
            "pnl_history_max_drawdown": pnl_stats["max_drawdown"],
            "top_tokens": "|".join(
                str(token.get("tokenSymbol") or token.get("tokenAddress") or "")
                for token in top_tokens
                if isinstance(token, dict)
            ),
            "profile_url": raw.get("profile_url", ""),
        })

    rows.sort(key=lambda item: (item["score"], item["pnl"]), reverse=True)
    selected = rows[: args.top]
    os.makedirs(args.data_dir, exist_ok=True)
    out_path = args.output or os.path.join(args.data_dir, "wallet_performance.csv")
    if selected:
        with open(out_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(selected[0].keys()))
            writer.writeheader()
            writer.writerows(selected)

    print(f"Saved wallet performance: {len(selected)} -> {out_path}\n")
    print("=== WALLET PERFORMANCE ===")
    for item in selected[: args.rows]:
        print(
            f"{item['score']:>7.2f} {item['wallet'][:10]:<10} "
            f"win={item['win_rate_pct']:>6.2f}% loss={item['estimated_loss_rate_pct']:>6.2f}% "
            f"tx={item['tx']:<5} pnl={item['pnl']:>10.2f} roi={item['roi_pct']:>8.2f}% "
            f"dd={item['pnl_history_max_drawdown']:>9.2f} {item['nickname']}"
        )
    return 0 if selected else 1


def cmd_monitor(args: argparse.Namespace) -> int:
    provider = make_provider("okx_web3", okx_url=args.okx_url)
    store = CopyTradeStore(args.data_dir)
    previous = None
    iteration = 0

    print("=== REALTIME MONITOR ===")
    print(f"Source:   {args.okx_url}")
    print(f"Interval: {args.interval}s")
    print(f"Alerts:   {os.path.join(args.data_dir, 'realtime_alerts.jsonl')}")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            iteration += 1
            try:
                state = _fetch_okx_monitor_state(provider, args.limit)
            except ProviderError as exc:
                print(f"[{utc_now_iso()}] fetch_error {exc}")
                time.sleep(args.interval)
                continue

            store.append_csv("realtime_trader_ticks.csv", state["traders"])
            store.append_jsonl("realtime_trader_ticks.jsonl", state["traders"])
            store.append_csv("realtime_token_ticks.csv", state["positions"])
            store.append_jsonl("realtime_token_ticks.jsonl", state["positions"])

            alerts = _build_monitor_alerts(
                current=state,
                previous=previous,
                min_token_wallets=args.min_token_wallets,
                emit_initial=args.emit_initial and previous is None,
            )
            if alerts:
                store.append_jsonl("realtime_alerts.jsonl", alerts)
                for alert in alerts[: args.rows]:
                    print(_format_alert(alert))
            else:
                print(
                    f"[{utc_now_iso()}] heartbeat "
                    f"wallets={len(state['wallets'])} tokens={len(state['tokens'])} alerts=0"
                )

            previous = state
            if args.iterations and iteration >= args.iterations:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped monitor.")
    return 0


def cmd_track_wallets(args: argparse.Namespace) -> int:
    print("Solana wallet tracking is no longer supported in this build.")
    return 1


def cmd_track_hyperliquid(args: argparse.Namespace) -> int:
    tracker = HyperliquidTracker()
    store = CopyTradeStore(args.data_dir)
    state_path = args.state_file or os.path.join(args.data_dir, "hyperliquid_tracker_state.json")
    state = load_hyperliquid_state(state_path)

    if args.wallets_csv:
        wallet_limit = args.wallet_limit + args.wallet_offset if args.wallet_limit else 0
        wallets = load_hyperliquid_wallets(args.wallets_csv, limit=wallet_limit)
        wallets = wallets[args.wallet_offset:]
        leaderboard_rows = []
    else:
        leaderboard_rows = tracker.fetch_leaderboard_wallets(
            url=args.leaderboard_url,
            limit=args.wallet_limit + args.wallet_offset,
            active_only=args.active_only,
        )
        leaderboard_rows = leaderboard_rows[args.wallet_offset:]
        wallets = [row["wallet"] for row in leaderboard_rows if row.get("wallet")]

    if not wallets:
        print("No Hyperliquid wallets found.")
        return 1

    if leaderboard_rows:
        _write_rows(os.path.join(args.data_dir, "hyperliquid_leaderboard.csv"), leaderboard_rows)

    print("=== HYPERLIQUID REALTIME TRACKER ===")
    print(f"Wallets:     {len(wallets)}")
    print(f"Source:      {args.wallets_csv or args.leaderboard_url}")
    print(f"Interval:    {args.interval}s")
    print(f"State:       {state_path}")
    print(f"Positions:   {os.path.join(args.data_dir, 'hyperliquid_position_events.csv')}")
    print(f"Fills:       {os.path.join(args.data_dir, 'hyperliquid_fill_events.csv')}")
    print("")

    iteration = 0
    try:
        while True:
            iteration += 1
            position_events = []
            fill_events = []
            for wallet in wallets:
                try:
                    p_events, f_events, state = tracker.collect_wallet(
                        wallet=wallet,
                        state=state,
                        emit_initial_positions=args.emit_initial and iteration == 1,
                        fill_limit=args.fill_limit,
                    )
                except Exception as exc:
                    print(f"[{utc_now_iso()}] hl_error wallet={wallet[:10]} {exc}")
                    continue
                position_events.extend(p_events)
                fill_events.extend(f_events)
                time.sleep(tracker.sleep_s)

            if position_events:
                store.append_csv("hyperliquid_position_events.csv", position_events)
                store.append_jsonl("hyperliquid_position_events.jsonl", position_events)
            if fill_events:
                store.append_csv("hyperliquid_fill_events.csv", fill_events)
                store.append_jsonl("hyperliquid_fill_events.jsonl", fill_events)

            for event in position_events[: args.rows]:
                print(
                    f"[{event.collected_at}] {event.wallet[:10]} "
                    f"{event.event_type.upper():<8} {event.side.upper():<5} {event.coin:<8} "
                    f"{event.previous_size:.6g}->{event.current_size:.6g} "
                    f"uPnL={_fmt(event.unrealized_pnl)} liq={_fmt(event.liquidation_price)}"
                )
            remaining = max(args.rows - len(position_events), 0)
            for event in fill_events[:remaining]:
                print(
                    f"[{event.fill_time}] {event.wallet[:10]} "
                    f"{event.direction:<14} {event.coin:<8} "
                    f"px={_fmt(event.price)} size={_fmt(event.size)} "
                    f"closedPnL={_fmt(event.closed_pnl)}"
                )

            if not position_events and not fill_events:
                active = sum(1 for wallet in wallets if (state.get(wallet, {}).get("positions") or {}))
                print(
                    f"[{utc_now_iso()}] heartbeat wallets={len(wallets)} "
                    f"active_positions_wallets={active} events=0"
                )

            save_hyperliquid_state(state_path, state)
            if args.iterations and iteration >= args.iterations:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        save_hyperliquid_state(state_path, state)
        print("\nStopped Hyperliquid tracker.")

    return 0


def cmd_hyperliquid_sweep(args: argparse.Namespace) -> int:
    tracker = HyperliquidTracker()
    all_rows: list[dict] = []
    seen_wallets: set[str] = set()
    offset = args.offset
    page = 0

    while True:
        if args.max_pages and page >= args.max_pages:
            break
        if args.max_wallets and len(all_rows) >= args.max_wallets:
            break

        url = (
            f"{args.leaderboard_url}"
            f"?window={args.window}&sort={args.sort}&order={args.order}"
            f"&limit={args.page_size}&offset={offset}"
        )
        rows = tracker.fetch_leaderboard_wallets(url=url, limit=args.page_size, active_only=False)
        if not rows:
            break

        added = 0
        for row in rows:
            wallet = row.get("wallet")
            if not wallet or wallet in seen_wallets:
                continue
            seen_wallets.add(wallet)
            row["roi_pct"] = _roi_to_pct(_to_float(row.get("roi")))
            all_rows.append(row)
            added += 1
            if args.max_wallets and len(all_rows) >= args.max_wallets:
                break

        page += 1
        active = sum(1 for row in rows if row.get("active_24h") is True)
        print(
            f"page={page:<3} offset={offset:<6} fetched={len(rows):<4} "
            f"added={added:<4} active={active:<4} total={len(all_rows)}"
        )
        if len(rows) < args.page_size or added == 0:
            break
        offset += args.page_size
        time.sleep(args.sleep)

    selected = []
    for row in all_rows:
        pnl = _to_float(row.get("pnl")) or 0.0
        roi_pct = _roi_to_pct(_to_float(row.get("roi")))
        account_value = _to_float(row.get("account_value")) or 0.0
        volume = _to_float(row.get("volume")) or 0.0
        if args.active_only and row.get("active_24h") is not True:
            continue
        if args.exclude_hft and row.get("is_hft") is True:
            continue
        if pnl < args.min_pnl:
            continue
        if roi_pct < args.min_roi_pct:
            continue
        if account_value < args.min_account_value:
            continue
        if volume < args.min_volume:
            continue
        selected.append(row)

    os.makedirs(args.data_dir, exist_ok=True)
    all_path = args.output_all or os.path.join(args.data_dir, "hyperliquid_leaderboard_sweep.csv")
    selected_path = args.output or os.path.join(args.data_dir, "hyperliquid_tracking_universe.csv")
    trusted_path = os.path.join(args.data_dir, "hyperliquid_leaderboard.csv")
    _write_rows(all_path, all_rows)
    _write_rows(selected_path, selected)
    _write_rows(trusted_path, selected)

    print("\n=== HYPERLIQUID SWEEP SUMMARY ===")
    print(f"Crawled wallets:     {len(all_rows)} -> {all_path}")
    print(f"Selected wallets:    {len(selected)} -> {selected_path}")
    print(f"Executor trusted:    {trusted_path}")
    print(
        f"Filters: pnl>={args.min_pnl:g}, roi>={args.min_roi_pct:g}%, "
        f"account>={args.min_account_value:g}, volume>={args.min_volume:g}, "
        f"active_only={args.active_only}, exclude_hft={args.exclude_hft}"
    )
    print("\nUse for tracking:")
    print(
        "python3 copy_trade_lab.py track-hyperliquid "
        f"--wallets-csv {selected_path} --wallet-limit {len(selected)} --iterations 0 --interval 3"
    )
    return 0


def cmd_trade_summary(args: argparse.Namespace) -> int:
    if not os.path.exists(args.events_csv):
        print(f"Missing events CSV: {args.events_csv}")
        return 2

    grouped: dict[str, dict[str, object]] = {}
    with open(args.events_csv, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            wallet = row.get("wallet") or ""
            if not wallet:
                continue
            item = grouped.setdefault(wallet, {
                "wallet": wallet,
                "buy_count": 0,
                "sell_count": 0,
                "quote_spent": 0.0,
                "quote_received": 0.0,
                "tokens": set(),
                "last_block_time": "",
            })
            action = row.get("action")
            quote_delta = _to_float(row.get("quote_delta")) or 0.0
            if action == "buy":
                item["buy_count"] = int(item["buy_count"]) + 1
                item["quote_spent"] = float(item["quote_spent"]) + abs(min(quote_delta, 0.0))
            elif action == "sell":
                item["sell_count"] = int(item["sell_count"]) + 1
                item["quote_received"] = float(item["quote_received"]) + max(quote_delta, 0.0)
            token = row.get("token_symbol") or row.get("token_mint") or ""
            if token:
                item["tokens"].add(token)
            if row.get("block_time", "") > str(item["last_block_time"]):
                item["last_block_time"] = row.get("block_time", "")

    rows = []
    for item in grouped.values():
        buy_count = int(item["buy_count"])
        sell_count = int(item["sell_count"])
        quote_spent = float(item["quote_spent"])
        quote_received = float(item["quote_received"])
        rows.append({
            "wallet": item["wallet"],
            "buy_count": buy_count,
            "sell_count": sell_count,
            "event_count": buy_count + sell_count,
            "quote_spent": round(quote_spent, 9),
            "quote_received": round(quote_received, 9),
            "quote_net": round(quote_received - quote_spent, 9),
            "tokens": "|".join(sorted(item["tokens"])),
            "last_block_time": item["last_block_time"],
        })

    rows.sort(key=lambda item: (item["last_block_time"], item["event_count"]), reverse=True)
    os.makedirs(args.data_dir, exist_ok=True)
    out_path = args.output or os.path.join(args.data_dir, "wallet_trade_summary.csv")
    if rows:
        with open(out_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    print(f"Saved trade summary: {len(rows)} -> {out_path}\n")
    print("=== WALLET TRADE SUMMARY ===")
    for item in rows[: args.rows]:
        print(
            f"{item['wallet'][:10]:<10} events={item['event_count']:<3} "
            f"buy={item['buy_count']:<3} sell={item['sell_count']:<3} "
            f"netSOL={item['quote_net']:>10.4f} tokens={str(item['tokens'])[:80]}"
        )
    return 0 if rows else 1


def cmd_browser_discover(args: argparse.Namespace) -> int:
    if args.mode == "requests":
        try:
            result = fetch_static_page(args.url, timeout=args.timeout)
        except Exception as exc:
            print(f"Requests scrape failed: {exc}")
            return 2
    else:
        browser = ChromeDumpBrowser(
            browser_path=args.browser_path,
            user_data_dir=args.user_data_dir,
            headless=not args.headful,
            timeout=args.timeout,
        )
        try:
            result = browser.dump_dom(args.url, wait_ms=args.wait_ms)
        except BrowserScrapeError as exc:
            print(f"Browser scrape failed: {exc}")
            return 2

    discovery = discover_page_with_assets(args.url, result.html, max_assets=args.max_assets)
    slug = args.slug or _slug_from_url(args.url)
    html_path, json_path = save_browser_artifacts(args.artifact_dir, slug, result.html, discovery)

    print("=== BROWSER DISCOVER ===")
    print(f"URL:          {args.url}")
    print(f"Browser:      {result.browser_path}")
    print(f"Title:        {discovery['title']}")
    print(f"HTML bytes:   {discovery['html_bytes']}")
    print(f"Assets:       {len(discovery.get('asset_urls', []))}")
    print(f"Artifacts:    {html_path}")
    print(f"Discovery:    {json_path}")
    print("\nAPI-like URLs:")
    for url in discovery["api_urls"][: args.rows]:
        print(f"  {url}")
    if not discovery["api_urls"]:
        print("  none found")
    print("\nText sample:")
    print(discovery["text_sample"][:1000])
    if discovery.get("asset_findings"):
        print("\nAsset string hints:")
        for finding in discovery["asset_findings"][:3]:
            print(f"  {finding['asset']}")
            for item in finding.get("strings", [])[:8]:
                print(f"    {item[:160]}")
    return 0


def cmd_browser_collect(args: argparse.Namespace) -> int:
    if args.mode == "requests":
        try:
            result = fetch_static_page(args.url, timeout=args.timeout)
        except Exception as exc:
            print(f"Requests scrape failed: {exc}")
            return 2
    else:
        browser = ChromeDumpBrowser(
            browser_path=args.browser_path,
            user_data_dir=args.user_data_dir,
            headless=not args.headful,
            timeout=args.timeout,
        )
        try:
            result = browser.dump_dom(args.url, wait_ms=args.wait_ms)
        except BrowserScrapeError as exc:
            print(f"Browser scrape failed: {exc}")
            return 2

    discovery = discover_page_with_assets(args.url, result.html, max_assets=args.max_assets)
    traders = parse_generic_traders(result.html, platform=args.platform)
    slug = args.slug or _slug_from_url(args.url)
    html_path, json_path = save_browser_artifacts(args.artifact_dir, slug, result.html, discovery)

    store = CopyTradeStore(args.data_dir)
    if traders:
        traders_path = store.append_csv("trader_daily_stats.csv", traders[: args.limit])
        store.append_jsonl("trader_daily_stats.jsonl", traders[: args.limit])
    else:
        traders_path = os.path.join(args.data_dir, "trader_daily_stats.csv")

    print("=== BROWSER COLLECT ===")
    print(f"URL:          {args.url}")
    print(f"Title:        {discovery['title']}")
    print(f"Traders:      {min(len(traders), args.limit)}")
    print(f"Artifacts:    {html_path}")
    print(f"Discovery:    {json_path}")
    print(f"Saved:        {traders_path if traders else 'none'}")
    if not traders:
        print("No trader JSON found. Use browser-discover output to identify API URLs or selectors.")
    return 0 if traders else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Copy-trade research lab")
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect")
    collect.add_argument("--provider", choices=["bitget", "binance", "csv", "polymarket", "hyperliquid", "okx_web3"], default="bitget")
    collect.add_argument("--limit", type=int, default=50)
    collect.add_argument("--with-positions", action="store_true")
    collect.add_argument("--traders-csv")
    collect.add_argument("--positions-csv")
    collect.add_argument("--wallets-csv")
    collect.add_argument("--category", default="CRYPTO")
    collect.add_argument("--time-period", choices=["DAY", "WEEK", "MONTH", "ALL"], default="MONTH")
    collect.add_argument("--order-by", choices=["PNL", "VOL"], default="PNL")
    collect.add_argument("--size-threshold", type=float, default=1)
    collect.add_argument("--okx-url", default="https://web3.okx.com/copy-trade/leaderboard/solana")
    collect.add_argument("--okx-chain-id", default="501")
    collect.add_argument("--okx-rank-by", choices=["pnl", "win_rate", "tx", "volume", "roi", "1", "2", "3", "4", "5"], default="pnl")
    collect.add_argument("--okx-period", choices=["1d", "7d", "30d", "1", "2", "3"], default="30d")
    collect.add_argument("--data-dir", default=DATA_DIR)

    okx_sweep = sub.add_parser("okx-sweep")
    okx_sweep.add_argument("--okx-url", default="https://web3.okx.com/copy-trade/leaderboard/solana")
    okx_sweep.add_argument("--chain-id", default="501", help="Deprecated: use --chain-ids")
    okx_sweep.add_argument("--chain-ids", default="501,1,56,8453",
                           help="Comma-separated OKX chain IDs: 501=Solana, 1=ETH, 56=BSC, 8453=Base, 137=Polygon")
    okx_sweep.add_argument("--rank-by", default="pnl,roi,win_rate,volume,tx")
    okx_sweep.add_argument("--periods", default="30d")
    okx_sweep.add_argument("--per-rank-limit", type=int, default=100)
    okx_sweep.add_argument("--max-wallets", type=int, default=300)
    okx_sweep.add_argument("--with-positions", action="store_true")
    okx_sweep.add_argument("--data-dir", default=DATA_DIR)

    consensus = sub.add_parser("consensus")
    consensus.add_argument("--traders-csv")
    consensus.add_argument("--positions-csv", required=True)
    consensus.add_argument("--limit", type=int, default=1000)
    consensus.add_argument("--top", type=int, default=10)
    consensus.add_argument("--threshold", type=float, default=0.70)
    consensus.add_argument("--max-drawdown", type=float)
    consensus.add_argument("--min-win-rate", type=float)
    consensus.add_argument("--min-copy-days", type=int)
    consensus.add_argument("--rows", type=int, default=20)
    consensus.add_argument("--data-dir", default=DATA_DIR)

    report = sub.add_parser("report")
    report.add_argument("--traders-csv", required=True)
    report.add_argument("--limit", type=int, default=1000)
    report.add_argument("--top", type=int, default=10)
    report.add_argument("--max-drawdown", type=float)
    report.add_argument("--min-win-rate", type=float)
    report.add_argument("--min-copy-days", type=int)

    watchlist = sub.add_parser("watchlist")
    watchlist.add_argument("--traders-csv", default=os.path.join(DATA_DIR, "trader_daily_stats.csv"))
    watchlist.add_argument("--platform", default="okx_web3")
    watchlist.add_argument("--top", type=int, default=10)
    watchlist.add_argument("--rows", type=int, default=20)
    watchlist.add_argument("--min-pnl", type=float, default=10000)
    watchlist.add_argument("--min-roi", type=float, default=10)
    watchlist.add_argument("--min-win-rate", type=float, default=20)
    watchlist.add_argument("--min-trades", type=int, default=20)
    watchlist.add_argument("--output")
    watchlist.add_argument("--data-dir", default=DATA_DIR)

    wallet_perf = sub.add_parser("wallet-performance")
    wallet_perf.add_argument("--traders-csv", default=os.path.join(DATA_DIR, "trader_daily_stats.csv"))
    wallet_perf.add_argument("--platform", default="okx_web3")
    wallet_perf.add_argument("--top", type=int, default=100)
    wallet_perf.add_argument("--rows", type=int, default=30)
    wallet_perf.add_argument("--min-trades", type=int, default=30, help="Conservative: 30+ trades")
    wallet_perf.add_argument("--min-pnl", type=float, default=1000)
    wallet_perf.add_argument("--min-win-rate", type=float, default=40, help="Conservative: 40%+")
    wallet_perf.add_argument("--output")
    wallet_perf.add_argument("--data-dir", default=DATA_DIR)

    monitor = sub.add_parser("monitor")
    monitor.add_argument("--provider", choices=["okx_web3"], default="okx_web3")
    monitor.add_argument("--okx-url", default="https://web3.okx.com/copy-trade/leaderboard/solana")
    monitor.add_argument("--limit", type=int, default=20)
    monitor.add_argument("--interval", type=float, default=30)
    monitor.add_argument("--iterations", type=int, default=0)
    monitor.add_argument("--min-token-wallets", type=int, default=2)
    monitor.add_argument("--emit-initial", action="store_true")
    monitor.add_argument("--rows", type=int, default=20)
    monitor.add_argument("--data-dir", default=DATA_DIR)

    track = sub.add_parser("track-wallets", help="(deprecated) Solana wallet tracking is no longer supported")
    track.add_argument("--data-dir", default=DATA_DIR)

    hl_sweep = sub.add_parser("hyperliquid-sweep")
    hl_sweep.add_argument("--leaderboard-url", default=DEXLY_LEADERBOARD_URL)
    hl_sweep.add_argument("--window", default="month", choices=["day", "week", "month", "all"])
    hl_sweep.add_argument("--sort", default="pnl")
    hl_sweep.add_argument("--order", default="desc")
    hl_sweep.add_argument("--page-size", type=int, default=100)
    hl_sweep.add_argument("--offset", type=int, default=0)
    hl_sweep.add_argument("--max-pages", type=int, default=0, help="0 = unlimited")
    hl_sweep.add_argument("--max-wallets", type=int, default=1000, help="0 = unlimited")
    hl_sweep.add_argument("--min-pnl", type=float, default=1_000_000)
    hl_sweep.add_argument("--min-roi-pct", type=float, default=80)
    hl_sweep.add_argument("--min-account-value", type=float, default=0)
    hl_sweep.add_argument("--min-volume", type=float, default=0)
    hl_sweep.add_argument("--active-only", action="store_true")
    hl_sweep.add_argument("--exclude-hft", action="store_true")
    hl_sweep.add_argument("--sleep", type=float, default=0.2)
    hl_sweep.add_argument("--output")
    hl_sweep.add_argument("--output-all")
    hl_sweep.add_argument("--data-dir", default=DATA_DIR)

    hl_track = sub.add_parser("track-hyperliquid")
    hl_track.add_argument("--wallets-csv")
    hl_track.add_argument("--leaderboard-url", default=DEXLY_LEADERBOARD_URL)
    hl_track.add_argument("--wallet-limit", type=int, default=10)
    hl_track.add_argument("--wallet-offset", type=int, default=0)
    hl_track.add_argument("--active-only", action="store_true")
    hl_track.add_argument("--emit-initial", action="store_true")
    hl_track.add_argument("--fill-limit", type=int, default=50)
    hl_track.add_argument("--interval", type=float, default=3)
    hl_track.add_argument("--iterations", type=int, default=1)
    hl_track.add_argument("--rows", type=int, default=30)
    hl_track.add_argument("--state-file")
    hl_track.add_argument("--data-dir", default=DATA_DIR)

    trade_summary = sub.add_parser("trade-summary")
    trade_summary.add_argument("--events-csv", default=os.path.join(DATA_DIR, "wallet_trade_events.csv"))
    trade_summary.add_argument("--rows", type=int, default=30)
    trade_summary.add_argument("--output")
    trade_summary.add_argument("--data-dir", default=DATA_DIR)

    dashboard = sub.add_parser("dashboard")
    dashboard.add_argument("--refresh", type=float, default=5)
    dashboard.add_argument("--iterations", type=int, default=0)
    dashboard.add_argument("--rows", type=int, default=10)
    dashboard.add_argument("--static", action="store_true")
    dashboard.add_argument("--data-dir", default=DATA_DIR)
    dashboard.add_argument("--collect", action="store_true", help="Enable live auto-collect pipeline")
    dashboard.add_argument("--collect-interval", type=float, default=120, help="Seconds between OKX sweeps")
    dashboard.add_argument("--track-interval", type=float, default=30, help="Seconds between wallet tx checks")
    dashboard.add_argument("--okx-url", default="https://web3.okx.com/copy-trade/leaderboard/solana")
    dashboard.add_argument("--okx-per-rank-limit", type=int, default=100)
    dashboard.add_argument("--okx-max-wallets", type=int, default=300)
    dashboard.add_argument("--track-wallet-limit", type=int, default=10)
    dashboard.add_argument("--track-tx-limit", type=int, default=8)

    browser_discover = sub.add_parser("browser-discover")
    browser_discover.add_argument("url")
    browser_discover.add_argument("--mode", choices=["chrome", "requests"], default="chrome")
    browser_discover.add_argument("--browser-path", default=os.getenv("CLOAK_BROWSER_PATH") or os.getenv("CHROME_PATH"))
    browser_discover.add_argument("--user-data-dir", default=os.getenv("CLOAK_USER_DATA_DIR"))
    browser_discover.add_argument("--headful", action="store_true")
    browser_discover.add_argument("--wait-ms", type=int, default=10000)
    browser_discover.add_argument("--timeout", type=int, default=60)
    browser_discover.add_argument("--artifact-dir", default=ARTIFACT_DIR)
    browser_discover.add_argument("--slug")
    browser_discover.add_argument("--rows", type=int, default=30)
    browser_discover.add_argument("--max-assets", type=int, default=20)

    browser_collect = sub.add_parser("browser-collect")
    browser_collect.add_argument("url")
    browser_collect.add_argument("--mode", choices=["chrome", "requests"], default="chrome")
    browser_collect.add_argument("--platform", default="browser")
    browser_collect.add_argument("--limit", type=int, default=50)
    browser_collect.add_argument("--browser-path", default=os.getenv("CLOAK_BROWSER_PATH") or os.getenv("CHROME_PATH"))
    browser_collect.add_argument("--user-data-dir", default=os.getenv("CLOAK_USER_DATA_DIR"))
    browser_collect.add_argument("--headful", action="store_true")
    browser_collect.add_argument("--wait-ms", type=int, default=10000)
    browser_collect.add_argument("--timeout", type=int, default=60)
    browser_collect.add_argument("--artifact-dir", default=ARTIFACT_DIR)
    browser_collect.add_argument("--data-dir", default=DATA_DIR)
    browser_collect.add_argument("--slug")
    browser_collect.add_argument("--max-assets", type=int, default=20)

    select_w = sub.add_parser("select-wallets")
    select_w.add_argument("--perf-csv", default=os.path.join(DATA_DIR, "wallet_performance.csv"))
    select_w.add_argument("--top", type=int, default=0, help="0 = unlimited")
    select_w.add_argument("--rows", type=int, default=50)
    select_w.add_argument("--min-win-rate", type=float, default=40, help="Conservative: 40%+")
    select_w.add_argument("--max-drawdown", type=float, default=40, help="Conservative: 40%<")
    select_w.add_argument("--min-trades", type=int, default=30, help="Conservative: 30+ trades")
    select_w.add_argument("--min-pnl", type=float, default=1000)
    select_w.add_argument("--min-roi", type=float, default=50, help="Conservative: 50%+")
    select_w.add_argument("--output")
    select_w.add_argument("--data-dir", default=DATA_DIR)

    pnl = sub.add_parser("pnl-report")
    pnl.add_argument("--trade-csv", default=os.path.join(DATA_DIR, "trade_history.csv"))
    pnl.add_argument("--rows", type=int, default=30)
    pnl.add_argument("--data-dir", default=DATA_DIR)

    execute = sub.add_parser("execute")
    execute.add_argument("--mode", choices=["binance", "hyperliquid", "both", "forex"], default="hyperliquid")
    execute.add_argument("--dry-run", action="store_true", default=True)
    execute.add_argument("--no-dry-run", action="store_false", dest="dry_run")
    execute.add_argument("--interval", type=float, default=60)
    execute.add_argument("--iterations", type=int, default=0)
    execute.add_argument("--max-positions", type=int, default=3)
    execute.add_argument("--position-size-usd", type=float, default=50)
    execute.add_argument("--min-confidence", type=float, default=0.60)
    execute.add_argument("--hl-min-delta-notional", type=float, default=1000)
    execute.add_argument("--hl-recent-seconds", type=int, default=900)
    execute.add_argument("--stop-loss-pct", type=float, default=8.0)
    execute.add_argument("--take-profit-pct", type=float, default=15.0)
    execute.add_argument("--max-daily-loss-pct", type=float, default=10.0)
    execute.add_argument("--max-consecutive-losses", type=int, default=3)
    execute.add_argument("--data-dir", default=DATA_DIR)

    forex_collect = sub.add_parser("forex-collect", help="Collect forex traders from MQL5 or CSV")
    forex_collect.add_argument("--provider", choices=["mql5", "csv"], default="mql5")
    forex_collect.add_argument("--limit", type=int, default=50)
    forex_collect.add_argument("--traders-csv")
    forex_collect.add_argument("--positions-csv")
    forex_collect.add_argument("--data-dir", default=DATA_DIR)

    forex_exec = sub.add_parser("forex-execute", help="Run forex copy trade executor")
    forex_exec.add_argument("--dry-run", action="store_true", default=True)
    forex_exec.add_argument("--no-dry-run", action="store_false", dest="dry_run")
    forex_exec.add_argument("--interval", type=float, default=60)
    forex_exec.add_argument("--iterations", type=int, default=0)
    forex_exec.add_argument("--max-positions", type=int, default=3)
    forex_exec.add_argument("--position-size-usd", type=float, default=1000)
    forex_exec.add_argument("--min-confidence", type=float, default=0.60)
    forex_exec.add_argument("--stop-loss-pct", type=float, default=5.0)
    forex_exec.add_argument("--take-profit-pct", type=float, default=10.0)
    forex_exec.add_argument("--max-daily-loss-pct", type=float, default=10.0)
    forex_exec.add_argument("--max-consecutive-losses", type=int, default=3)
    forex_exec.add_argument("--data-dir", default=DATA_DIR)

    return parser.parse_args()


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _write_rows(path: str, rows: list[dict]) -> None:
    if not rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _slug_from_url(url: str) -> str:
    import re

    return re.sub(r"^https?://", "", url).strip("/").replace("/", "_")


def _to_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace("%", "").replace("$", "").replace(",", ""))
    except ValueError:
        return None


def _roi_to_pct(value: float | None) -> float:
    if value is None:
        return 0.0
    if abs(value) <= 10:
        return value * 100
    return value


def _json_loads(value: str | None) -> dict:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _split_csv_arg(value: str) -> list[str]:
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _pnl_history_stats(history: list[dict]) -> dict[str, float]:
    values = []
    for item in history:
        if isinstance(item, dict):
            value = _to_float(item.get("pnl"))
            if value is not None:
                values.append(value)
    if not values:
        return {"change": 0.0, "max_drawdown": 0.0}
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value - peak)
    return {
        "change": round(values[-1] - values[0], 6),
        "max_drawdown": round(max_drawdown, 6),
    }


def _wallet_score(
    pnl: float,
    roi: float,
    win_rate: float,
    tx: float,
    volume: float | None,
) -> float:
    volume = volume or 0
    return (
        math.log10(max(pnl, 1)) * 2.0
        + min(roi, 1000) / 100.0
        + win_rate / 20.0
        + math.log10(max(tx, 1))
        + math.log10(max(volume, 1)) / 2.0
    )


def _fetch_okx_monitor_state(provider: object, limit: int) -> dict:
    traders = provider.fetch_traders(limit=limit)
    positions = []
    for trader in traders:
        positions.extend(provider.fetch_positions(trader.trader_id))

    wallets = {trader.trader_id: trader for trader in traders}
    wallet_tokens = {}
    tokens: dict[str, set[str]] = {}
    for pos in positions:
        symbol = pos.symbol.upper()
        wallet_tokens.setdefault(pos.trader_id, set()).add(symbol)
        tokens.setdefault(symbol, set()).add(pos.trader_id)

    return {
        "collected_at": utc_now_iso(),
        "traders": traders,
        "positions": positions,
        "wallets": wallets,
        "wallet_tokens": wallet_tokens,
        "tokens": tokens,
    }


def _build_monitor_alerts(
    current: dict,
    previous: dict | None,
    min_token_wallets: int,
    emit_initial: bool,
) -> list[dict]:
    now = current["collected_at"]
    alerts = []

    previous_wallets = set((previous or {}).get("wallets", {}))
    previous_wallet_tokens = (previous or {}).get("wallet_tokens", {})
    previous_consensus = {
        token
        for token, wallets in (previous or {}).get("tokens", {}).items()
        if len(wallets) >= min_token_wallets
    }

    for wallet, trader in current["wallets"].items():
        if emit_initial or (previous is not None and wallet not in previous_wallets):
            alerts.append({
                "collected_at": now,
                "type": "new_wallet",
                "wallet": wallet,
                "nickname": trader.nickname,
                "rank": trader.rank,
                "pnl_30d": trader.pnl_30d,
                "roi_30d": trader.roi_30d,
                "win_rate": trader.win_rate,
            })

    for wallet, tokens in current["wallet_tokens"].items():
        old_tokens = previous_wallet_tokens.get(wallet, set())
        for token in sorted(tokens - old_tokens):
            if emit_initial or previous is not None:
                alerts.append({
                    "collected_at": now,
                    "type": "new_top_token",
                    "wallet": wallet,
                    "token": token,
                    "token_wallet_count": len(current["tokens"].get(token, [])),
                })

    for token, wallets in sorted(current["tokens"].items()):
        if len(wallets) < min_token_wallets:
            continue
        if emit_initial or token not in previous_consensus:
            alerts.append({
                "collected_at": now,
                "type": "token_consensus",
                "token": token,
                "wallet_count": len(wallets),
                "wallets": "|".join(sorted(wallets)),
            })

    return alerts


def _format_alert(alert: dict) -> str:
    prefix = f"[{alert.get('collected_at')}] {alert.get('type')}"
    if alert.get("type") == "new_wallet":
        return (
            f"{prefix} wallet={alert.get('wallet', '')[:10]} "
            f"rank={alert.get('rank')} roi={_fmt(alert.get('roi_30d'))} "
            f"win={_fmt(alert.get('win_rate'))} {alert.get('nickname', '')}"
        )
    if alert.get("type") == "new_top_token":
        return (
            f"{prefix} token={alert.get('token')} "
            f"wallet={alert.get('wallet', '')[:10]} "
            f"wallet_count={alert.get('token_wallet_count')}"
        )
    if alert.get("type") == "token_consensus":
        return (
            f"{prefix} token={alert.get('token')} "
            f"wallet_count={alert.get('wallet_count')}"
        )
    return f"{prefix} {json.dumps(alert, ensure_ascii=False, sort_keys=True)}"


def cmd_select_wallets(args: argparse.Namespace) -> int:
    if not os.path.exists(args.perf_csv):
        print(f"Missing performance CSV: {args.perf_csv}")
        print("Run 'wallet-performance' first.")
        return 2

    rows = []
    with open(args.perf_csv, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            wr = _to_float(row.get("win_rate_pct"))
            dd = _to_float(row.get("pnl_history_max_drawdown"))
            tx = _to_float(row.get("tx"))
            pnl = _to_float(row.get("pnl"))
            roi = _to_float(row.get("roi_pct"))
            if wr is not None and wr < args.min_win_rate:
                continue
            if dd is not None and dd < -abs(args.max_drawdown):
                continue
            if tx is not None and tx < args.min_trades:
                continue
            if pnl is not None and pnl < args.min_pnl:
                continue
            if roi is not None and roi < args.min_roi:
                continue
            rows.append(row)

    rows.sort(key=lambda r: _to_float(r.get("score")) or 0, reverse=True)
    selected = rows[: args.top] if args.top else rows

    out_path = args.output or os.path.join(args.data_dir, "wallet_selection.csv")
    os.makedirs(args.data_dir, exist_ok=True)
    fieldnames = [
        "platform", "trader_id", "wallet", "nickname", "rank", "score",
        "win_rate", "win_rate_pct", "total_trades", "tx", "pnl_30d", "pnl",
        "drawdown", "pnl_history_max_drawdown", "top_tokens", "profile_url",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for rank, row in enumerate(selected, 1):
            raw_drawdown = _to_float(row.get("pnl_history_max_drawdown"))
            out = {k: row.get(k, "") for k in fieldnames}
            out["platform"] = row.get("platform") or "okx_web3"
            out["trader_id"] = row.get("trader_id") or row.get("wallet", "")
            out["rank"] = rank
            out["win_rate"] = row.get("win_rate_pct", "")
            out["total_trades"] = row.get("tx", "")
            out["pnl_30d"] = row.get("pnl", "")
            out["drawdown"] = abs(raw_drawdown) if raw_drawdown is not None else ""
            writer.writerow(out)

    print(f"Saved selection: {len(selected)} -> {out_path}\n")
    print("=== SELECTED WALLETS ===")
    for idx, row in enumerate(selected, 1):
        print(
            f"  #{idx:<2} {row.get('wallet', '')[:10]:<10} "
            f"score={_to_float(row.get('score')) or 0:>7.2f} "
            f"win={_to_float(row.get('win_rate_pct')) or 0:>6.1f}% "
            f"tx={int(_to_float(row.get('tx')) or 0):<5} "
            f"pnl=${_to_float(row.get('pnl')) or 0:>8,.0f} "
            f"{row.get('nickname', '')}"
        )
    return 0


def cmd_pnl_report(args: argparse.Namespace) -> int:
    if not os.path.exists(args.trade_csv):
        print(f"No trade history: {args.trade_csv}")
        return 2

    trades = []
    with open(args.trade_csv, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            trades.append(row)

    if not trades:
        print("No trades recorded.")
        return 1

    # Tính PnL ước tính cho các lệnh đang mở (không phải dry-run)
    open_trades = [t for t in trades if t.get("action") == "open" and t.get("dry_run") == "False"]
    closed_trades = [t for t in trades if "close" in (t.get("action") or "")]
    total_live = len(open_trades)
    total_dry = len([t for t in trades if t.get("dry_run") == "True" and t.get("action") == "open"])

    # Thử fetch current price để tính floating PnL
    live_pnl = 0.0
    pnl_rows = []
    for t in open_trades:
        price_str = t.get("price", "")
        if not price_str:
            continue
        entry = _to_float(price_str) or 0
        symbol = t.get("symbol", "")
        size = _to_float(t.get("size_usd")) or 0
        side = t.get("side", "buy")
        try:
            import ccxt
            ex = ccxt.binance({"enableRateLimit": True})
            ticker = ex.fetch_ticker(symbol)
            current = ticker["last"]
            if side == "buy":
                pnl_pct = (current - entry) / entry
            else:
                pnl_pct = (entry - current) / entry
            pnl_usd = size * pnl_pct
            live_pnl += pnl_usd
            pnl_rows.append((symbol, side, entry, current, pnl_pct * 100, pnl_usd, "LIVE"))
        except Exception:
            pnl_rows.append((symbol, side, entry, 0, 0, 0, "N/A"))

    print("\n=== COPY TRADE PnL REPORT ===")
    print(f"Total trades logged:    {len(trades)}")
    print(f"  ├─ Live executed:     {total_live}")
    print(f"  ├─ Dry-run signals:   {total_dry}")
    print(f"  └─ Closed positions:  {len(closed_trades)}")
    print()

    if pnl_rows:
        print("Open positions (floating PnL):")
        print(f"  {'Symbol':<12} {'Side':<6} {'Entry':<10} {'Current':<10} {'PnL%':<8} {'PnL $':<10}")
        print(f"  {'-'*56}")
        for sym, side, entry, cur, pnl_pct, pnl_usd, status in pnl_rows:
            marker = "●" if status == "LIVE" else "○"
            print(f"  {marker} {sym:<10} {side:<6} {entry:<10.2f} {cur:<10.2f} {pnl_pct:<+7.2f}% {pnl_usd:<+9.2f}")
        print(f"\n  Estimated total floating PnL: ${live_pnl:+.2f}")
    else:
        print("No live positions with price data (dry-run only or missing prices).")

    print()
    print("Recent signals (last 10):")
    print(f"  {'Time':<20} {'Action':<10} {'Symbol':<12} {'Side':<6} {'Mode':<8}")
    print(f"  {'-'*56}")
    for t in trades[-10:]:
        ts = t.get("timestamp", "")[11:19]
        mode = "DRY" if t.get("dry_run") == "True" else "LIVE"
        print(f"  {ts:<20} {t.get('action',''):<10} {t.get('symbol',''):<12} {t.get('side',''):<6} {mode:<8}")
    return 0


def cmd_forex_collect(args: argparse.Namespace) -> int:
    from copy_trade.forex_provider import make_forex_provider
    from copy_trade.storage import CopyTradeStore

    provider = make_forex_provider(
        args.provider,
        traders_csv=args.traders_csv,
        positions_csv=args.positions_csv,
    )
    store = CopyTradeStore(args.data_dir)

    try:
        traders = provider.fetch_traders(limit=args.limit)
    except Exception as exc:
        print(f"Forex collect failed: {exc}")
        return 2

    traders_path = store.append_csv("forex_traders.csv", traders)
    store.append_jsonl("forex_traders.jsonl", traders)
    print(f"Saved forex traders: {len(traders)} -> {traders_path}")

    if args.provider == "csv" and args.positions_csv:
        csv_provider = make_forex_provider("csv", traders_csv=args.traders_csv, positions_csv=args.positions_csv)
        positions = csv_provider.fetch_all_positions()
        if positions:
            positions_path = store.append_csv("forex_positions.csv", positions)
            store.append_jsonl("forex_positions.jsonl", positions)
            print(f"Saved forex positions: {len(positions)} -> {positions_path}")

    return 0


def cmd_forex_execute(args: argparse.Namespace) -> int:
    from copy_trade.executor import CopyTradeOrchestrator
    from copy_trade.forex_executor import build_forex_executor

    orch = CopyTradeOrchestrator(data_dir=args.data_dir, dry_run=args.dry_run)
    executor = build_forex_executor(
        data_dir=args.data_dir,
        dry_run=args.dry_run,
        interval=args.interval,
        max_positions=args.max_positions,
        position_size_usd=args.position_size_usd,
        min_confidence=args.min_confidence,
        stop_loss_pct=args.stop_loss_pct,
        take_profit_pct=args.take_profit_pct,
        max_daily_loss_pct=args.max_daily_loss_pct,
        max_consecutive_losses=args.max_consecutive_losses,
    )
    orch.add(executor)
    print(f"Forex executor (dry_run={args.dry_run}, interval={args.interval}s)")
    orch.run_loop(iterations=args.iterations)
    return 0


def cmd_execute(args: argparse.Namespace) -> int:
    from copy_trade.executor import (
        CopyTradeOrchestrator,
        build_binance_executor,
        build_hyperliquid_executor,
    )
    from copy_trade.forex_executor import build_forex_executor

    orch = CopyTradeOrchestrator(data_dir=args.data_dir, dry_run=args.dry_run)

    if args.mode in ("binance", "both"):
        executor = build_binance_executor(
            data_dir=args.data_dir,
            dry_run=args.dry_run,
            interval=args.interval,
            max_positions=args.max_positions,
            position_size_usd=args.position_size_usd,
            min_confidence=args.min_confidence,
            stop_loss_pct=args.stop_loss_pct,
            take_profit_pct=args.take_profit_pct,
            max_daily_loss_pct=args.max_daily_loss_pct,
            max_consecutive_losses=args.max_consecutive_losses,
        )
        orch.add(executor)
        print(f"Binance executor added (dry_run={args.dry_run}, interval={args.interval}s)")

    if args.mode in ("hyperliquid", "both"):
        executor = build_hyperliquid_executor(
            data_dir=args.data_dir,
            dry_run=args.dry_run,
            interval=args.interval,
            max_positions=args.max_positions,
            position_size_usd=args.position_size_usd,
            min_confidence=args.min_confidence,
            min_delta_notional=args.hl_min_delta_notional,
            recent_seconds=args.hl_recent_seconds,
            stop_loss_pct=args.stop_loss_pct,
            take_profit_pct=args.take_profit_pct,
            max_daily_loss_pct=args.max_daily_loss_pct,
            max_consecutive_losses=args.max_consecutive_losses,
        )
        orch.add(executor)
        print(f"Hyperliquid executor added (dry_run={args.dry_run}, interval={args.interval}s)")

    if args.mode == "forex":
        executor = build_forex_executor(
            data_dir=args.data_dir,
            dry_run=args.dry_run,
            interval=args.interval,
            max_positions=args.max_positions,
            position_size_usd=args.position_size_usd * 10,
            min_confidence=args.min_confidence,
            stop_loss_pct=args.stop_loss_pct,
            take_profit_pct=args.take_profit_pct,
            max_daily_loss_pct=args.max_daily_loss_pct,
            max_consecutive_losses=args.max_consecutive_losses,
        )
        orch.add(executor)
        print(f"Forex executor added (dry_run={args.dry_run}, interval={args.interval}s)")

    print(f"\nStarting copy trade execution...")
    orch.run_loop(iterations=args.iterations)
    return 0


def main() -> int:
    args = parse_args()
    if args.command == "collect":
        return cmd_collect(args)
    if args.command == "okx-sweep":
        return cmd_okx_sweep(args)
    if args.command == "consensus":
        return cmd_consensus(args)
    if args.command == "report":
        return cmd_report(args)
    if args.command == "watchlist":
        return cmd_watchlist(args)
    if args.command == "wallet-performance":
        return cmd_wallet_performance(args)
    if args.command == "monitor":
        return cmd_monitor(args)
    if args.command == "track-wallets":
        return cmd_track_wallets(args)
    if args.command == "hyperliquid-sweep":
        return cmd_hyperliquid_sweep(args)
    if args.command == "track-hyperliquid":
        return cmd_track_hyperliquid(args)
    if args.command == "trade-summary":
        return cmd_trade_summary(args)
    if args.command == "dashboard":
        dashboard = CopyTradeDashboard(
            data_dir=args.data_dir,
            refresh=args.refresh,
            rows=args.rows,
            collect=args.collect,
            collect_interval=args.collect_interval,
            track_interval=args.track_interval,
            okx_url=args.okx_url,
            okx_per_rank_limit=args.okx_per_rank_limit,
            okx_max_wallets=args.okx_max_wallets,
            track_wallet_limit=args.track_wallet_limit,
            track_tx_limit=args.track_tx_limit,
        )
        if args.static:
            dashboard.print_once()
        else:
            dashboard.run(iterations=args.iterations)
        return 0
    if args.command == "browser-discover":
        return cmd_browser_discover(args)
    if args.command == "browser-collect":
        return cmd_browser_collect(args)
    if args.command == "select-wallets":
        return cmd_select_wallets(args)
    if args.command == "pnl-report":
        return cmd_pnl_report(args)
    if args.command == "execute":
        return cmd_execute(args)
    if args.command == "forex-collect":
        return cmd_forex_collect(args)
    if args.command == "forex-execute":
        return cmd_forex_execute(args)
    raise ValueError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
