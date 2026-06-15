#!/usr/bin/env python3
"""
Copy Trade Bot — Binance Futures (Hyperliquid cross-venue).

Pipeline (mỗi cycle):
  1. Collect OKX Web3 Solana leaderboard
  2. Score wallet performance
  3. Select potential wallets
  4. Build consensus signals
  5. Track Hyperliquid wallet positions
  6. Execute copy trades on Binance Futures

Usage:
  python3 main_copy_trade.py                  # daemon mode (mặc định dry-run)
  python3 main_copy_trade.py --once            # chạy 1 cycle rồi thoát
  python3 main_copy_trade.py --no-dry-run      # đánh thật (cần API keys)
  python3 main_copy_trade.py --mode binance    # chỉ chạy Binance Futures executor
  python3 main_copy_trade.py --mode hyperliquid # chỉ chạy Hyperliquid executor
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from typing import Any

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from apscheduler.schedulers.blocking import BlockingScheduler

from dotenv import load_dotenv

from copy_trade.storage import CopyTradeStore
from notify import Notifier

load_dotenv()
DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "copy_trade")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("copy_trade_bot")


class CopyTradeBot:
    def __init__(
        self,
        mode: str = "hyperliquid",
        dry_run: bool = True,
        interval_minutes: int = 15,
        collect_interval: int = 120,
        max_positions: int = 6,
        position_size_usd: float = 100,
        min_confidence: float = 0.65,
        slippage_bps: int = 200,
        okx_max_wallets: int = 300,
    ):
        self.mode = mode
        self.dry_run = dry_run
        self.interval_minutes = interval_minutes
        self.data_dir = DATA_DIR
        self.store = CopyTradeStore(DATA_DIR)
        self.notifier = Notifier()

        self.max_positions = max_positions
        self.position_size_usd = position_size_usd
        self.min_confidence = min_confidence
        self.slippage_bps = slippage_bps
        self.okx_max_wallets = okx_max_wallets
        self.collect_interval = collect_interval

        self._last_collect = 0.0
        self._binance_executor = None
        self._hyperliquid_executor = None
        self.stats: dict[str, Any] = {
            "cycles": 0,
            "binance_signals": 0,
            "hyperliquid_signals": 0,
            "binance_trades": 0,
            "hyperliquid_trades": 0,
            "errors": 0,
            "last_cycle_time": "",
            "last_error": "",
        }

    # ── Pipeline steps ────────────────────────────────────

    def collect_okx(self) -> None:
        """Step 1: OKX sweep — lấy traders + positions mới nhất."""
        logger.info("[1/5] Collecting OKX Web3 traders...")
        try:
            from copy_trade_lab import cmd_okx_sweep
            import argparse

            args = argparse.Namespace(
                okx_url="https://web3.okx.com/copy-trade/leaderboard/solana",
                chain_id="501",
                rank_by="pnl,roi,win_rate,volume,tx",
                periods="30d",
                per_rank_limit=100,
                max_wallets=self.okx_max_wallets,
                with_positions=True,
                data_dir=self.data_dir,
            )
            cmd_okx_sweep(args)
            logger.info("[1/5] OKX collect OK")
        except Exception as exc:
            logger.warning("[1/5] OKX collect failed: %s", exc)
            self.stats["errors"] += 1
            self.stats["last_error"] = f"collect: {exc}"

    def score_wallets(self) -> None:
        """Step 2: Wallet performance — chấm điểm ví."""
        logger.info("[2/5] Scoring wallet performance...")
        try:
            from copy_trade_lab import cmd_wallet_performance
            import argparse

            csv_path = os.path.join(self.data_dir, "trader_daily_stats.csv")
            if not os.path.exists(csv_path):
                logger.warning("[2/5] No trader data yet, skip scoring")
                return

            args = argparse.Namespace(
                traders_csv=csv_path,
                platform="okx_web3",
                top=150,
                rows=30,
                min_trades=3,
                min_pnl=100,
                min_win_rate=0,
                output="",
                data_dir=self.data_dir,
            )
            cmd_wallet_performance(args)
            logger.info("[2/5] Scoring OK")
        except Exception as exc:
            logger.warning("[2/5] Scoring failed: %s", exc)

    def select_wallets(self) -> None:
        """Step 2.5: Chọn ví tiềm năng từ performance data."""
        logger.info("[2.5/5] Selecting potential wallets...")
        try:
            from copy_trade_lab import cmd_select_wallets
            import argparse

            perf_csv = os.path.join(self.data_dir, "wallet_performance.csv")
            if not os.path.exists(perf_csv):
                logger.warning("[2.5/5] No performance data, skip selection")
                return

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
            cmd_select_wallets(args)
            logger.info("[2.5/5] Selection OK")
        except Exception as exc:
            logger.warning("[2.5/5] Selection failed: %s", exc)

    def build_consensus(self) -> None:
        """Step 3: Consensus signals — chỉ từ selected wallets."""
        logger.info("[3/5] Building consensus from selected wallets...")
        try:
            from copy_trade_lab import cmd_consensus
            import argparse

            positions_csv = os.path.join(self.data_dir, "trader_positions.csv")
            selection_csv = os.path.join(self.data_dir, "wallet_selection.csv")
            if not os.path.exists(positions_csv):
                logger.warning("[3/5] No positions data, skip consensus")
                return

            traders_csv = selection_csv if os.path.exists(selection_csv) else ""

            args = argparse.Namespace(
                traders_csv=traders_csv,
                positions_csv=positions_csv,
                limit=1000,
                top=15,
                threshold=0.60,
                max_drawdown=None,
                min_win_rate=None,
                min_copy_days=None,
                rows=30,
                data_dir=self.data_dir,
            )
            cmd_consensus(args)
            logger.info("[3/5] Consensus OK")
        except Exception as exc:
            logger.warning("[3/5] Consensus failed: %s", exc)

    def track_hyperliquid(self) -> None:
        """Step 4: Track Hyperliquid wallet positions + fills."""
        logger.info("[4/5] Tracking Hyperliquid wallets...")
        try:
            from copy_trade_lab import cmd_track_hyperliquid
            import argparse

            args = argparse.Namespace(
                wallets_csv=os.path.join(self.data_dir, "hyperliquid_tracking_universe.csv"),
                leaderboard_url="https://dexly.trade/hyperliquid/leaderboard",
                wallet_limit=10,
                wallet_offset=0,
                active_only=False,
                emit_initial=False,
                fill_limit=50,
                interval=3,
                iterations=1,
                rows=30,
                state_file=os.path.join(self.data_dir, "hyperliquid_tracker_state.json"),
                data_dir=self.data_dir,
            )
            cmd_track_hyperliquid(args)
            logger.info("[4/5] Tracking OK")
        except Exception as exc:
            logger.warning("[4/5] Tracking failed: %s", exc)

    def execute_trades(self) -> None:
        """Step 5: Execute copy trades."""
        logger.info("[5/5] Executing copy trades (dry_run=%s, mode=%s)...", self.dry_run, self.mode)

        if self.mode in ("binance", "both"):
            self._execute_binance()
        if self.mode in ("hyperliquid", "both"):
            self._execute_hyperliquid()

    def _get_binance_executor(self):
        if self._binance_executor is None:
            from copy_trade.executor import build_binance_executor
            self._binance_executor = build_binance_executor(
                data_dir=self.data_dir,
                dry_run=self.dry_run,
                interval=9999,
                max_positions=self.max_positions,
                position_size_usd=self.position_size_usd,
                min_confidence=self.min_confidence,
                stop_loss_pct=8.0,
                take_profit_pct=15.0,
                max_daily_loss_pct=10.0,
                max_consecutive_losses=3,
                total_capital=10000.0,
            )
        return self._binance_executor

    def _get_hyperliquid_executor(self):
        if self._hyperliquid_executor is None:
            from copy_trade.executor import build_hyperliquid_executor
            self._hyperliquid_executor = build_hyperliquid_executor(
                data_dir=self.data_dir,
                dry_run=self.dry_run,
                interval=9999,
                max_positions=self.max_positions,
                position_size_usd=self.position_size_usd,
                min_confidence=max(self.min_confidence, 0.65),
                min_delta_notional=100.0,
                recent_seconds=604800,
                stop_loss_pct=8.0,
                take_profit_pct=15.0,
                max_daily_loss_pct=10.0,
                max_consecutive_losses=3,
                total_capital=10000.0,
            )
        return self._hyperliquid_executor

    def _execute_binance(self) -> None:
        try:
            ex = self._get_binance_executor()
            signals = ex.run_once()
            self.stats["binance_signals"] += len(signals)
            self.stats["binance_trades"] = len(ex.active_positions)

            for pos in ex.active_positions.values():
                sig = pos["signal"]
                logger.info(
                    "[5/5] Binance ACTIVE %s %s $%.0f (%.0f%% conf, %d traders)",
                    sig.side.upper(), sig.symbol, sig.size_usd,
                    sig.confidence * 100, sig.trader_count,
                )
            logger.info("[5/5] Binance: %d new signals, %d active positions", len(signals), len(ex.active_positions))
        except Exception as exc:
            logger.warning("[5/5] Binance execution error: %s", exc)
            self.stats["errors"] += 1

    def _execute_hyperliquid(self) -> None:
        try:
            ex = self._get_hyperliquid_executor()
            signals = ex.run_once()
            self.stats["hyperliquid_signals"] += len(signals)
            self.stats["hyperliquid_trades"] = len(ex.active_positions)

            for pos in ex.active_positions.values():
                sig = pos["signal"]
                logger.info(
                    "[5/5] Hyperliquid ACTIVE %s %s $%.0f (%.0f%% conf, trader=%s)",
                    sig.side.upper(), sig.symbol, sig.size_usd,
                    sig.confidence * 100, sig.source_symbol,
                )
            logger.info("[5/5] Hyperliquid: %d new signals, %d active positions", len(signals), len(ex.active_positions))
        except Exception as exc:
            logger.warning("[5/5] Hyperliquid execution error: %s", exc)
            self.stats["errors"] += 1

    # ── Main cycle ────────────────────────────────────────

    def run_cycle(self) -> None:
        """Run one full pipeline cycle."""
        now = time.time()
        logger.info("=" * 50)
        logger.info("CYCLE START  %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        logger.info("=" * 50)

        try:
            if now - self._last_collect >= self.collect_interval:
                self.collect_okx()
                self.score_wallets()
                self.select_wallets()
                self.build_consensus()
                self.track_hyperliquid()
                self._last_collect = now

            self.execute_trades()
        except Exception as exc:
            logger.exception("Cycle error: %s", exc)
            self.stats["errors"] += 1
            self.stats["last_error"] = str(exc)

        self.stats["cycles"] += 1
        self.stats["last_cycle_time"] = datetime.now().strftime("%H:%M:%S")

        logger.info("-" * 50)
        logger.info("CYCLE DONE  | cycles=%d | bin=%d/%d | hl=%d/%d | errors=%d",
                     self.stats["cycles"],
                     self.stats["binance_signals"], self.stats["binance_trades"],
                     self.stats["hyperliquid_signals"], self.stats["hyperliquid_trades"],
                     self.stats["errors"])
        logger.info("=" * 50)

    def send_report(self) -> None:
        """Gửi báo cáo Telegram sau mỗi cycle."""
        if not self.notifier.enabled:
            return
        try:
            mode_str = "DRY-RUN" if self.dry_run else "LIVE"
            msg = (
                f"<b>Copy Trade Bot — {mode_str}</b>\n"
                f"Mode: {self.mode.upper()}\n"
                f"Cycles: {self.stats['cycles']}\n"
                f"Binance Futures: {self.stats['binance_trades']} active / {self.stats['binance_signals']} total\n"
                f"Hyperliquid: {self.stats['hyperliquid_trades']} active / {self.stats['hyperliquid_signals']} total\n"
                f"Errors: {self.stats['errors']}"
            )
            if self.stats["last_error"]:
                msg += f"\nLast error: {self.stats['last_error']}"
            self.notifier.send(msg)
        except Exception as exc:
            logger.warning("Report send failed: %s", exc)

    # ── Scheduler ─────────────────────────────────────────

    def run_daemon(self) -> None:
        """Chạy nền với APScheduler."""
        testnet = os.getenv("BINANCE_TESTNET", "true")
        self.notifier.send(
            f"<b>Copy Trade Bot started</b>\n"
            f"Mode: {self.mode.upper()}\n"
            f"Dry-run: {self.dry_run}\n"
            f"Interval: {self.interval_minutes}m\n"
            f"Binance Futures testnet: {testnet}"
        )

        scheduler = BlockingScheduler()

        @scheduler.scheduled_job("interval", minutes=self.interval_minutes, id="copy_trade_cycle")
        def job():
            self.run_cycle()
            self.send_report()

        logger.info("Starting daemon: every %d minutes (dry_run=%s, mode=%s)",
                     self.interval_minutes, self.dry_run, self.mode)
        try:
            scheduler.start()
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            scheduler.shutdown()
            self.notifier.send("Copy Trade Bot stopped")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Copy Trade Bot — Binance Futures (Hyperliquid cross-venue)")
    parser.add_argument("--once", action="store_true", help="Run one cycle then exit")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Paper trading / không đánh thật")
    parser.add_argument("--no-dry-run", action="store_false", dest="dry_run",
                        help="Đánh thật (cần API keys)")
    parser.add_argument("--mode", choices=["binance", "hyperliquid", "both"], default="hyperliquid")
    parser.add_argument("--interval", type=int, default=15, help="Minutes between cycles")
    parser.add_argument("--collect-interval", type=int, default=120, help="Seconds between OKX sweeps")
    parser.add_argument("--max-positions", type=int, default=3)
    parser.add_argument("--position-size-usd", type=float, default=100)
    parser.add_argument("--min-confidence", type=float, default=0.65)
    parser.add_argument("--slippage-bps", type=int, default=200)
    parser.add_argument("--okx-max-wallets", type=int, default=300)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    bot = CopyTradeBot(
        mode=args.mode,
        dry_run=args.dry_run,
        interval_minutes=args.interval,
        collect_interval=args.collect_interval,
        max_positions=args.max_positions,
        position_size_usd=args.position_size_usd,
        min_confidence=args.min_confidence,
        slippage_bps=args.slippage_bps,
        okx_max_wallets=args.okx_max_wallets,
    )

    if args.once:
        bot.run_cycle()
        bot.send_report()
    else:
        bot.run_daemon()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
