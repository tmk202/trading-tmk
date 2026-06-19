from __future__ import annotations

import csv
import json
import logging
import re
from typing import Any

import requests

from copy_trade.models import PositionSnapshot, TraderSnapshot, utc_now_iso
from copy_trade.providers import CopyTradeProvider, ProviderError

logger = logging.getLogger(__name__)

MQL5_SIGNALS_URL = "https://www.mql5.com/en/signals/mt5/list"


class MQL5SignalsProvider(CopyTradeProvider):
    """Scrape MQL5.com signals leaderboard (free, no API key)."""

    name = "mql5"

    def __init__(
        self,
        sort_by: str = "Growth",
        min_growth: float = 0,
        page_size: int = 48,
    ):
        self.sort_by = sort_by
        self.min_growth = min_growth
        self.page_size = page_size
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": MQL5_SIGNALS_URL,
        })

    def fetch_traders(self, limit: int = 50) -> list[TraderSnapshot]:
        rows = self._fetch_signal_rows(limit)
        if not rows:
            raise ProviderError(
                "MQL5 signals leaderboard returned no rows. "
                "The page may have changed or require a different approach."
            )
        return [self._row_to_trader(row, idx + 1) for idx, row in enumerate(rows[:limit])]

    def _fetch_signal_rows(self, limit: int) -> list[dict[str, Any]]:
        """Try internal API first, fall back to HTML scraping."""
        rows = self._fetch_api_rows(limit)
        if rows:
            return rows
        return self._fetch_html_rows(limit)

    def _fetch_api_rows(self, limit: int) -> list[dict[str, Any]]:
        """Try MQL5 internal API endpoints."""
        endpoints = [
            (
                "https://www.mql5.com/en/api/v1/signals/list",
                {
                    "page": 1,
                    "per_page": min(limit, 48),
                    "sort": "growth",
                    "order": "desc",
                    "min_subscribers": 0,
                },
            ),
            (
                "https://www.mql5.com/en/signal/mt5/rating",
                {
                    "page": 1,
                    "limit": min(limit, 48),
                    "sort_by": "growth_desc",
                },
            ),
        ]
        for url, params in endpoints:
            try:
                resp = self.session.get(url, params=params, timeout=25)
                if resp.status_code >= 400:
                    continue
                data = resp.json()
                rows = (
                    data.get("signals")
                    or data.get("list")
                    or data.get("items")
                    or data.get("data")
                    or []
                )
                if isinstance(rows, list) and len(rows) > 0:
                    logger.info("MQL5 API returned %d signals", len(rows))
                    return rows
            except Exception:
                continue
        return []

    def _fetch_html_rows(self, limit: int) -> list[dict[str, Any]]:
        """Parse HTML leaderboard table."""
        try:
            resp = self.session.get(
                MQL5_SIGNALS_URL,
                params={"page": 1},
                timeout=25,
            )
            resp.raise_for_status()
        except Exception as exc:
            raise ProviderError(f"MQL5 page fetch failed: {exc}") from exc

        return self._parse_signal_table(resp.text, limit)

    def _parse_signal_table(self, html: str, limit: int) -> list[dict[str, Any]]:
        """Extract signal rows from HTML page."""
        rows: list[dict[str, Any]] = []

        # Try to find JSON-LD or embedded data first
        json_patterns = [
            r'window\.__INITIAL_STATE__\s*=\s*({.+?});',
            r'"signals"\s*:\s*(\[.+?\])',
            r'data-signals=\'(.+?)\'',
        ]
        for pattern in json_patterns:
            match = re.search(pattern, html, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1))
                    if isinstance(data, dict):
                        signals = data.get("signals") or data.get("list") or []
                        if isinstance(signals, list):
                            for s in signals[:limit]:
                                if isinstance(s, dict):
                                    rows.append(s)
                    elif isinstance(data, list):
                        rows = data[:limit]
                    if rows:
                        return rows
                except (json.JSONDecodeError, KeyError):
                    continue

        # Fall back to HTML table parsing
        table_pattern = re.compile(
            r'<tr[^>]*>\s*<td[^>]*>.*?(\d+)</td>\s*'
            r'<td[^>]*>.*?<a[^>]*>(.*?)</a>.*?</td>\s*'
            r'<td[^>]*>(.*?)</td>\s*'
            r'<td[^>]*>(.*?)</td>\s*'
            r'<td[^>]*>(.*?)</td>\s*'
            r'<td[^>]*>(.*?)</td>',
            re.DOTALL | re.IGNORECASE,
        )
        matches = table_pattern.findall(html)
        for match in matches[:limit]:
            if len(match) >= 6:
                rows.append({
                    "id": _clean_html(match[0]),
                    "name": _clean_html(match[1]),
                    "growth": _clean_html(match[2]),
                    "profit": _clean_html(match[3]),
                    "subscribers": _clean_html(match[4]),
                    "trades": _clean_html(match[5]),
                })

        # Try alternate table structure
        if not rows:
            alt_pattern = re.compile(
                r'<tr[^>]*data-signal-id="(\d+)"[^>]*>.*?'
                r'data-name="([^"]+)".*?'
                r'data-growth="([^"]+)".*?'
                r'data-profit="([^"]+)".*?'
                r'data-subscribers="([^"]+)".*?'
                r'data-trades="([^"]+)"',
                re.DOTALL | re.IGNORECASE,
            )
            for match in alt_pattern.findall(html)[:limit]:
                rows.append({
                    "id": match[0],
                    "name": match[1],
                    "growth": match[2],
                    "profit": match[3],
                    "subscribers": match[4],
                    "trades": match[5],
                })

        return rows

    def _row_to_trader(self, row: dict[str, Any], rank: int) -> TraderSnapshot:
        trader_id = str(
            row.get("id")
            or row.get("signal_id")
            or row.get("author_id")
            or row.get("login")
            or f"mql5_{rank}"
        )
        nickname = str(
            row.get("name")
            or row.get("signal_name")
            or row.get("author_name")
            or row.get("title")
            or trader_id
        )
        growth = _to_float(
            row.get("growth")
            or row.get("gain")
            or row.get("totalGrowth")
        )
        profit_usd = _to_float(
            row.get("profit")
            or row.get("totalProfit")
            or row.get("pnl")
        )
        subscribers = _to_int(
            row.get("subscribers")
            or row.get("subscriberCount")
        )
        trades = _to_int(
            row.get("trades")
            or row.get("totalTrades")
            or row.get("tradeCount")
        )
        drawdown = _to_float(
            row.get("drawdown")
            or row.get("maxDrawdown")
            or row.get("dd")
        )
        win_rate = _to_float(
            row.get("winRate")
            or row.get("win_rate")
            or row.get("profitableTradesPercent")
        )

        return TraderSnapshot(
            collected_at=utc_now_iso(),
            platform=self.name,
            trader_id=trader_id,
            nickname=nickname,
            rank=rank,
            roi_30d=growth,
            pnl_30d=profit_usd,
            drawdown=drawdown,
            win_rate=win_rate,
            total_trades=trades,
            followers=subscribers,
            raw={
                **row,
                "source": "mql5_signals",
                "profile_url": f"https://www.mql5.com/en/signals/{trader_id}",
            },
        )


class ForexCsvProvider(CopyTradeProvider):
    """Import forex trader data from CSV files."""

    name = "forex_csv"

    def __init__(
        self,
        traders_csv: str | None = None,
        positions_csv: str | None = None,
    ):
        self.traders_csv = traders_csv
        self.positions_csv = positions_csv

    def fetch_traders(self, limit: int = 50) -> list[TraderSnapshot]:
        if not self.traders_csv:
            raise ProviderError("--traders-csv is required for forex_csv provider")
        collected_at = utc_now_iso()
        rows = _read_csv(self.traders_csv)[:limit]
        return [
            self._row_to_trader(row, collected_at, idx + 1)
            for idx, row in enumerate(rows)
        ]

    def fetch_all_positions(self) -> list[PositionSnapshot]:
        if not self.positions_csv:
            return []
        collected_at = utc_now_iso()
        return [
            self._row_to_position(row, collected_at)
            for row in _read_csv(self.positions_csv)
        ]

    def _row_to_trader(
        self, row: dict[str, str], collected_at: str, rank: int
    ) -> TraderSnapshot:
        return TraderSnapshot(
            collected_at=collected_at,
            platform=row.get("platform", "forex"),
            trader_id=row.get("trader_id") or row.get("wallet") or f"forex_{rank}",
            nickname=row.get("nickname", ""),
            rank=_to_int(row.get("rank")) or rank,
            roi_30d=_to_float(row.get("roi_30d") or row.get("growth")),
            pnl_30d=_to_float(row.get("pnl_30d") or row.get("pnl")),
            drawdown=_to_float(row.get("drawdown") or row.get("max_drawdown")),
            win_rate=_to_float(row.get("win_rate")),
            total_trades=_to_int(row.get("total_trades") or row.get("trades")),
            raw=row,
        )

    def _row_to_position(
        self, row: dict[str, str], collected_at: str
    ) -> PositionSnapshot:
        from copy_trade.forex_executor import _normalize_forex_symbol

        symbol = row.get("symbol") or row.get("pair") or ""
        side = (row.get("side") or row.get("direction") or "").lower()
        if side in ("long",):
            side = "buy"
        elif side in ("short",):
            side = "sell"

        return PositionSnapshot(
            collected_at=collected_at,
            platform=row.get("platform", "forex"),
            trader_id=row.get("trader_id") or row.get("wallet") or "",
            symbol=_normalize_forex_symbol(symbol),
            side=side,
            entry_price=_to_float(row.get("entry_price")),
            mark_price=_to_float(row.get("current_price") or row.get("mark_price")),
            size=_to_float(row.get("size") or row.get("volume") or row.get("lots")),
            notional=_to_float(row.get("notional") or row.get("value")),
            pnl=_to_float(row.get("pnl")),
            pnl_pct=_to_float(row.get("pnl_pct")),
            raw=row,
        )


def make_forex_provider(
    name: str,
    traders_csv: str | None = None,
    positions_csv: str | None = None,
    **kwargs,
) -> CopyTradeProvider:
    if name == "mql5":
        return MQL5SignalsProvider(
            sort_by=kwargs.get("sort_by", "Growth"),
            min_growth=kwargs.get("min_growth", 0),
        )
    if name in ("forex_csv", "forex-csv"):
        return ForexCsvProvider(
            traders_csv=traders_csv,
            positions_csv=positions_csv,
        )
    if name == "csv":
        return ForexCsvProvider(
            traders_csv=traders_csv,
            positions_csv=positions_csv,
        )
    raise ValueError(f"Unknown forex provider: {name}")


def _read_csv(path: str) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace("%", "").replace("$", "").replace(",", ""))
    except ValueError:
        return None


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except ValueError:
        return None


def _clean_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()
