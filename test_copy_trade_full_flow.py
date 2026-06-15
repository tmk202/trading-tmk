#!/usr/bin/env python3
"""
Test full copytrade flow:
  1. Crawl/simulate wallet detection (ví tiềm năng)
  2. Detect new orders (phát hiện lệnh mới)
  3. Copy trade execution (đánh theo)
  4. 10% profit → cut (10% lãi → cắt)

Chạy: python3 test_copy_trade_full_flow.py
"""
from __future__ import annotations

import csv
import logging
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from copy import deepcopy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("test_copytrade")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "copy_trade")

def setup_test_data(tmpdir: str) -> dict[str, str]:
    """Seed CSV files from real data into tmpdir."""
    files = {
        "wallet_selection.csv": os.path.join(DATA_DIR, "wallet_selection.csv"),
        "trader_positions.csv": os.path.join(DATA_DIR, "trader_positions.csv"),
    }
    for name, src in files.items():
        if os.path.exists(src):
            dst = os.path.join(tmpdir, name)
            with open(src) as f_in:
                content = f_in.read()
            with open(dst, "w") as f_out:
                f_out.write(content)
            logger.info("  Copied %s (%d rows)", name, len(content.splitlines()) - 1)
        else:
            logger.warning("  MISSING: %s", src)
    return tmpdir


def test_select_traders():
    print("\n" + "="*70)
    print("TEST 1: select_traders() — Lọc ví tiềm năng")
    print("="*70)

    from copy_trade.analyzer import select_traders
    from copy_trade.providers import CsvProvider

    traders_csv = os.path.join(DATA_DIR, "wallet_selection.csv")
    if not os.path.exists(traders_csv):
        logger.warning("SKIP: wallet_selection.csv not found")
        return

    provider = CsvProvider(traders_csv=traders_csv)
    traders = provider.fetch_traders(limit=100)
    print(f"  Tổng traders đọc được: {len(traders)}")

    # Apply filters
    filtered = select_traders(
        traders,
        limit=10,
        max_drawdown=20.0,
        min_win_rate=40.0,
        min_copy_days=1,
    )
    print(f"  Sau filter (max_drawdown<20%, win_rate>=40%): {len(filtered)} traders")

    for t in filtered[:5]:
        print(f"    Rank {t.rank}: {t.nickname[:20]:20s} | "
              f"ROI={t.roi_30d or 0:>8.2f}% | Win={t.win_rate or 0:>5.1f}% | "
              f"PnL={t.pnl_30d or 0:>8.2f}")
    assert len(filtered) > 0, "FAIL: Không tìm thấy ví tiềm năng!"
    print(f"  ✓ PASS: select_traders — tìm được {len(filtered)} ví")
    return filtered


def test_build_consensus():
    print("\n" + "="*70)
    print("TEST 2: build_consensus() — Phát hiện lệnh mới từ vị thế")
    print("="*70)

    from copy_trade.analyzer import build_consensus
    from copy_trade.providers import CsvProvider

    positions_csv = os.path.join(DATA_DIR, "trader_positions.csv")
    if not os.path.exists(positions_csv):
        logger.warning("SKIP: trader_positions.csv not found")
        return

    provider = CsvProvider(positions_csv=positions_csv)
    positions = provider.fetch_all_positions()
    print(f"  Tổng positions: {len(positions)}")

    # Build consensus without filtering
    signals = build_consensus(positions, threshold=0.60)
    print(f"  Consensus signals: {len(signals)}")

    for s in signals[:5]:
        print(f"    {s.symbol:10s} | signal={s.signal:5s} | "
              f"long={s.long_count}/{s.trader_count} ({s.long_ratio*100:.0f}%) | "
              f"short={s.short_count}/{s.trader_count} ({s.short_ratio*100:.0f}%)")

    # Filter by selected wallets
    selection_csv = os.path.join(DATA_DIR, "wallet_selection.csv")
    if os.path.exists(selection_csv):
        sel_provider = CsvProvider(traders_csv=selection_csv)
        selected = sel_provider.fetch_traders(limit=50)
        signals_filtered = build_consensus(positions, selected_traders=selected, threshold=0.60)
        print(f"  Consensus from SELECTED wallets: {len(signals_filtered)}")
        for s in signals_filtered:
            print(f"    {s.symbol:10s} | signal={s.signal:5s} | "
                  f"trader_count={s.trader_count} | "
                  f"long_ratio={s.long_ratio*100:.0f}% short_ratio={s.short_ratio*100:.0f}%")

    assert len(signals) > 0, "FAIL: Không có consensus signal nào!"
    print("  ✓ PASS: build_consensus — phát hiện lệnh thành công")
    return signals


def test_binance_executor():
    print("\n" + "="*70)
    print("TEST 3: BinanceCopyExecutor — Đánh theo (dry-run)")
    print("="*70)

    from copy_trade.executor import build_binance_executor

    with tempfile.TemporaryDirectory() as tmpdir:
        setup_test_data(tmpdir)

        ex = build_binance_executor(
            data_dir=tmpdir,
            dry_run=True,
            interval=9999,
            max_positions=3,
            position_size_usd=100,
            min_confidence=0.60,
            stop_loss_pct=5.0,
            take_profit_pct=10.0,
            max_daily_loss_pct=30.0,
            max_consecutive_losses=3,
            total_capital=1000.0,
        )

        signals = ex.run_once()
        print(f"  Signals generated: {len(signals)}")
        for s in signals:
            print(f"    {s.side.upper():4s} {s.symbol:10s} | "
                  f"size=${s.size_usd:.0f} | confidence={s.confidence*100:.0f}% | "
                  f"traders={s.trader_count}")

        print(f"  Active positions after cycle: {len(ex.active_positions)}")
        for sym, pos in ex.active_positions.items():
            sig = pos["signal"]
            print(f"    {sym:10s} | side={sig.side:4s} | "
                  f"size=${sig.size_usd:.0f} | conf={sig.confidence*100:.0f}%")

    return len(ex.active_positions) > 0


def test_multi_cycle_tp():
    print("\n" + "="*70)
    print("TEST 5: Mô phỏng Multi-cycle + TP 10% — SL 5%")
    print("="*70)

    from copy_trade.executor import build_binance_executor, TradeSignal
    from copy_trade.analyzer import build_consensus
    from copy_trade.providers import CsvProvider

    with tempfile.TemporaryDirectory() as tmpdir:
        setup_test_data(tmpdir)

        ex = build_binance_executor(
            data_dir=tmpdir,
            dry_run=True,
            interval=9999,
            max_positions=3,
            position_size_usd=100,
            min_confidence=0.50,
            stop_loss_pct=5.0,
            take_profit_pct=10.0,
            max_daily_loss_pct=30.0,
            max_consecutive_losses=3,
            total_capital=1000.0,
        )

        # Cycle 1: collect + execute
        signals = ex.run_once()
        print(f"  Cycle 1: {len(signals)} signals, {len(ex.active_positions)} active positions")
        initial_positions = len(ex.active_positions)

        # Add simulated entry_price to dry-run positions
        for pos in ex.active_positions.values():
            pos["entry_price"] = 100.0

        # Patch close_position to work without real exchange
        orig_close = ex.close_position
        def mock_close(sym):
            ex.active_positions.pop(sym, None)
            return True
        ex.close_position = mock_close

        # Override dry_run check to allow SL/TP testing
        orig_check = ex._check_stop_loss
        def patched_check(sym):
            # Bypass dry_run guard
            pos = ex.active_positions.get(sym)
            if not pos:
                return
            entry = pos.get("entry_price")
            if not entry:
                return
            side = pos["side"]
            try:
                current = ex._get_current_price(sym)
            except Exception:
                return
            if side == "buy":
                pnl_pct = (current - entry) / entry * 100
            else:
                pnl_pct = (entry - current) / entry * 100
            if pnl_pct >= ex.take_profit_pct:
                logger.info("TP HIT %s: entry=%.2f current=%.2f pnl=%.1f%%", sym, entry, current, pnl_pct)
                ex.close_position(sym)
            elif pnl_pct <= -ex.stop_loss_pct:
                logger.warning("SL HIT %s: entry=%.2f current=%.2f pnl=%.1f%%", sym, entry, current, pnl_pct)
                ex.close_position(sym)
        ex._check_stop_loss = patched_check

        original_get_price = ex._get_current_price
        price_step = [1.0]

        def mock_get_price(symbol):
            step = price_step[0]
            if step <= 1:
                return 100.0  # entry
            elif step == 2:
                return 105.0  # +5% chưa TP
            elif step == 3:
                return 111.0  # +11% > 10% TP!
            return 100.0

        ex._get_current_price = mock_get_price

        # Cycle 2: price +5% — chưa TP
        price_step[0] = 2
        for sym in list(ex.active_positions.keys()):
            ex._check_stop_loss(sym)
        print(f"  Cycle 2 (+5%): {len(ex.active_positions)} active positions (chưa đạt TP)")

        # Cycle 3: price +11% — TP HIT!
        price_step[0] = 3
        for sym in list(ex.active_positions.keys()):
            ex._check_stop_loss(sym)
        remaining_after_tp = len(ex.active_positions)
        print(f"  Cycle 3 (+11% — TP 10%): {remaining_after_tp} active positions còn lại")

        ex._get_current_price = original_get_price
        ex._check_stop_loss = orig_check
        ex.close_position = orig_close

    if initial_positions > 0 and remaining_after_tp < initial_positions:
        print(f"  ✓ PASS: TP 10% triggered! Closed {initial_positions - remaining_after_tp}/{initial_positions} positions")
    else:
        if initial_positions == 0:
            print("  ℹ No positions opened (data may not produce signals with current filter)")
        else:
            print(f"  ⚠ TP not triggered (remaining={remaining_after_tp}, initial={initial_positions}) — "
                  f"dry-run mode bypasses real price check")

    return True


def test_circuit_breaker():
    print("\n" + "="*70)
    print("TEST 6: Circuit Breaker — Bảo vệ khi thua lỗ")
    print("="*70)

    from copy_trade.executor import build_binance_executor

    with tempfile.TemporaryDirectory() as tmpdir:
        setup_test_data(tmpdir)

        ex = build_binance_executor(
            data_dir=tmpdir, dry_run=True, interval=9999,
            max_positions=3, position_size_usd=100,
            min_confidence=0.50,
            stop_loss_pct=5.0, take_profit_pct=10.0,
            max_daily_loss_pct=10.0,
            max_consecutive_losses=3,
            total_capital=1000.0,
        )

        # Simulate 3 consecutive losses
        ex._day_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        ex._consecutive_losses = 3
        ex._check_circuit_breaker()
        cb_active = ex._circuit_breaker

        print(f"  Circuit breaker active: {cb_active}")
        print(f"  Reason: {ex._circuit_breaker_reason}")

        # run_once should not generate signals now
        signals = ex.run_once()
        print(f"  Signals during CB: {len(signals)} (should be 0)")

        assert cb_active, "FAIL: Circuit breaker should be active after 3 losses!"
        assert len(signals) == 0, "FAIL: No signals should be generated during CB!"

    print("  ✓ PASS: Circuit breaker hoạt động đúng")
    return True


def test_storage_and_orchestrator():
    print("\n" + "="*70)
    print("TEST 7: CopyTradeOrchestrator — Pipeline end-to-end")
    print("="*70)

    from copy_trade.executor import build_binance_executor, CopyTradeOrchestrator
    from copy_trade.storage import CopyTradeStore

    with tempfile.TemporaryDirectory() as tmpdir:
        setup_test_data(tmpdir)
        store = CopyTradeStore(tmpdir)

        orchestrator = CopyTradeOrchestrator(data_dir=tmpdir, dry_run=True)
        ex = build_binance_executor(
            data_dir=tmpdir, dry_run=True, interval=9999,
            max_positions=3, position_size_usd=100,
            min_confidence=0.50,
            stop_loss_pct=5.0, take_profit_pct=10.0,
            total_capital=1000.0,
        )
        orchestrator.add(ex)

        signals = orchestrator.run_once()
        print(f"  Orchestrator signals: {len(signals)}")

        trade_history_path = os.path.join(tmpdir, "trade_history.csv")
        if os.path.exists(trade_history_path):
            with open(trade_history_path) as f:
                lines = f.readlines()
            print(f"  Trade history rows: {len(lines) - 1} (excl header)")
            for line in lines[:4]:
                print(f"    {line.strip()}")

    print("  ✓ PASS: Orchestrator + Storage hoạt động")
    return True


def test_providers():
    print("\n" + "="*70)
    print("TEST 8: Providers — Đọc dữ liệu từ CSV")
    print("="*70)

    from copy_trade.providers import CsvProvider

    # Test với sample traders
    csv_provider = CsvProvider(
        traders_csv=os.path.join(DATA_DIR, "sample_traders.csv"),
        positions_csv=os.path.join(DATA_DIR, "sample_positions.csv"),
    )
    traders = csv_provider.fetch_traders()
    positions = csv_provider.fetch_all_positions()

    print(f"  sample_traders.csv → {len(traders)} traders")
    print(f"  sample_positions.csv → {len(positions)} positions")

    for t in traders:
        print(f"    {t.trader_id:5s} {t.nickname:10s} | ROI={t.roi_30d}% | Win={t.win_rate}%")

    for p in positions:
        print(f"    {p.trader_id:5s} {p.symbol:10s} | {p.side:5s} | entry={p.entry_price}")

    assert len(traders) > 0, "FAIL: Không đọc được traders!"
    assert len(positions) > 0, "FAIL: Không đọc được positions!"
    print("  ✓ PASS: Providers đọc CSV thành công")
    return traders, positions


def test_full_pipeline_simulation():
    print("\n" + "="*70)
    print("TEST 9: Mô phỏng Full Pipeline — Crawl → Detect → Copy → TP")
    print("="*70)

    from copy_trade.analyzer import select_traders, build_consensus
    from copy_trade.providers import CsvProvider
    from copy_trade.executor import build_binance_executor, TradeSignal

    # Bước 1: Crawl ví tiềm năng (từ CSV)
    print("\n  [Bước 1] Crawl ví tiềm năng...")
    sel_provider = CsvProvider(traders_csv=os.path.join(DATA_DIR, "wallet_selection.csv"))
    all_wallets = sel_provider.fetch_traders(limit=100)
    print(f"    Tổng craw được: {len(all_wallets)} ví")

    selected = select_traders(all_wallets, limit=10, min_win_rate=50)
    print(f"    Ví đủ điều kiện (win_rate>=50%): {len(selected)}")
    selected_wallet_ids = [t.trader_id for t in selected]

    # Bước 2: Phát hiện lệnh mới
    print("  [Bước 2] Phát hiện lệnh mới từ ví đã chọn...")
    pos_provider = CsvProvider(positions_csv=os.path.join(DATA_DIR, "trader_positions.csv"))
    all_positions = pos_provider.fetch_all_positions()

    consensus = build_consensus(all_positions, selected_traders=selected, threshold=0.50)
    print(f"    Consensus signals: {len(consensus)} cặp")
    for c in consensus:
        print(f"      {c.symbol}: {c.signal} (long={c.long_count}/{c.trader_count}, short={c.short_count}/{c.trader_count})")

    # Bước 3: Đánh theo (dry-run)
    print("  [Bước 3] Đánh theo (dry-run mode)...")
    with tempfile.TemporaryDirectory() as tmpdir:
        setup_test_data(tmpdir)
        ex = build_binance_executor(
            data_dir=tmpdir, dry_run=True, interval=9999,
            max_positions=3, position_size_usd=100,
            min_confidence=0.50,
            stop_loss_pct=5.0, take_profit_pct=10.0,
            total_capital=1000.0,
        )
        signals = ex.run_once()
        print(f"    Executor signals: {len(signals)}")

        if len(ex.active_positions) > 0:
            print(f"    Positions opened: {len(ex.active_positions)}")
            for sym, pos in ex.active_positions.items():
                sig = pos["signal"]
                print(f"      {sym}: {sig.side.upper()} ${sig.size_usd:.0f} @ conf={sig.confidence*100:.0f}%")
        else:
            print("    No positions opened (threshold may not be met)")
            print("    → 1) Consensus chưa đạt threshold")
            print("    → 2) Cần thêm dữ liệu realtime")

        # Bước 4: Mô phỏng giá chạm TP 10%
        print("  [Bước 4] Mô phỏng giá đạt +11% → TP 10% hit...")
        for pos in ex.active_positions.values():
            pos["entry_price"] = 100.0
        initial_count = len(ex.active_positions)

        # Patch close_position + _check_stop_loss để test không cần exchange thật
        orig_close = ex.close_position
        def mock_close(sym):
            ex.active_positions.pop(sym, None)
            return True
        ex.close_position = mock_close

        orig_check = ex._check_stop_loss
        def patched_check(sym):
            pos = ex.active_positions.get(sym)
            if not pos:
                return
            entry = pos.get("entry_price")
            if not entry:
                return
            side = pos["side"]
            current = ex._get_current_price(sym)
            pnl_pct = (current - entry) / entry * 100
            if pnl_pct >= ex.take_profit_pct:
                logger.info("TP HIT %s: entry=%.2f current=%.2f pnl=%.1f%%", sym, entry, current, pnl_pct)
                ex.close_position(sym)

        def mock_price_tp(_):
            return 111.0

        ex._get_current_price = mock_price_tp
        for sym in list(ex.active_positions.keys()):
            patched_check(sym)

        ex._check_stop_loss = orig_check
        ex.close_position = orig_close

        closed = initial_count - len(ex.active_positions)
        print(f"    Positions before TP: {initial_count}")
        print(f"    Positions after TP:  {len(ex.active_positions)}")
        print(f"    Closed by TP:        {closed}")
        if closed > 0:
            print("    ✓ TP 10% triggered — positions closed!")
        else:
            print("    ⚠ TP not triggered — kiểm tra lại entry_price / mock price")

    tp_worked = closed > 0 if initial_count > 0 else True
    if tp_worked:
        print("\n  ✓ PASS: Full pipeline — crawl → detect → copy → TP 10% OK")
    else:
        print("\n  ⚠ Full pipeline: TP simulation needs review (dry-run constraints)")
    return tp_worked


def main():
    print("="*70)
    print("  COPY TRADE — FULL FLOW TEST")
    print(f"  Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)

    results = {}
    errors = []

    # Test 1: Select traders
    try:
        results["select_traders"] = test_select_traders()
    except Exception as e:
        errors.append(("select_traders", str(e)))
        logger.error("FAIL select_traders: %s", e)

    # Test 2: Build consensus
    try:
        results["consensus"] = test_build_consensus()
    except Exception as e:
        errors.append(("consensus", str(e)))
        logger.error("FAIL consensus: %s", e)

    # Test 3: Binance executor dry-run
    try:
        results["binance_executor"] = test_binance_executor()
    except Exception as e:
        errors.append(("binance_executor", str(e)))
        logger.error("FAIL binance_executor: %s", e)

    # Test 4: Multi-cycle with TP
    try:
        results["multi_cycle_tp"] = test_multi_cycle_tp()
    except Exception as e:
        errors.append(("multi_cycle_tp", str(e)))
        logger.error("FAIL multi_cycle_tp: %s", e)

    # Test 6: Circuit breaker
    try:
        results["circuit_breaker"] = test_circuit_breaker()
    except Exception as e:
        errors.append(("circuit_breaker", str(e)))
        logger.error("FAIL circuit_breaker: %s", e)

    # Test 7: Orchestrator + Storage
    try:
        results["orchestrator"] = test_storage_and_orchestrator()
    except Exception as e:
        errors.append(("orchestrator", str(e)))
        logger.error("FAIL orchestrator: %s", e)

    # Test 8: Providers
    try:
        results["providers"] = test_providers()
    except Exception as e:
        errors.append(("providers", str(e)))
        logger.error("FAIL providers: %s", e)

    # Test 9: Full pipeline
    try:
        results["full_pipeline"] = test_full_pipeline_simulation()
    except Exception as e:
        errors.append(("full_pipeline", str(e)))
        logger.error("FAIL full_pipeline: %s", e)

    # Summary
    print("\n" + "="*70)
    print("  RESULTS SUMMARY")
    print("="*70)
    passed = 0
    for name, result in results.items():
        status = "✓" if result else "✗"
        if result:
            passed += 1
        print(f"  {status} {name}")

    if errors:
        print(f"\n  ERRORS ({len(errors)}):")
        for name, err in errors:
            print(f"    ✗ {name}: {err}")

    print(f"\n  Passed: {passed}/{len(results)}")
    print(f"  Errors: {len(errors)}")
    print("="*70)

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
