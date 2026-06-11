from __future__ import annotations

import csv
import json
import re
from abc import ABC, abstractmethod
from typing import Any

import requests

from copy_trade.models import PositionSnapshot, TraderSnapshot, utc_now_iso


class ProviderError(RuntimeError):
    pass


class CopyTradeProvider(ABC):
    name: str

    @abstractmethod
    def fetch_traders(self, limit: int = 50) -> list[TraderSnapshot]:
        raise NotImplementedError

    def fetch_positions(self, trader_id: str) -> list[PositionSnapshot]:
        return []


class CsvProvider(CopyTradeProvider):
    name = "csv"

    def __init__(self, traders_csv: str | None = None, positions_csv: str | None = None):
        self.traders_csv = traders_csv
        self.positions_csv = positions_csv

    def fetch_traders(self, limit: int = 50) -> list[TraderSnapshot]:
        if not self.traders_csv:
            raise ProviderError("--traders-csv is required for csv provider")
        collected_at = utc_now_iso()
        rows = _read_csv(self.traders_csv)[:limit]
        return [self._row_to_trader(row, collected_at, idx + 1) for idx, row in enumerate(rows)]

    def fetch_all_positions(self) -> list[PositionSnapshot]:
        if not self.positions_csv:
            return []
        collected_at = utc_now_iso()
        return [self._row_to_position(row, collected_at) for row in _read_csv(self.positions_csv)]

    def _row_to_trader(self, row: dict[str, str], collected_at: str, rank: int) -> TraderSnapshot:
        return TraderSnapshot(
            collected_at=collected_at,
            platform=row.get("platform", "csv"),
            trader_id=row.get("trader_id") or row.get("uid") or row.get("id") or f"csv_{rank}",
            nickname=row.get("nickname", ""),
            rank=_to_int(row.get("rank")) or rank,
            roi_7d=_to_float(row.get("roi_7d")),
            roi_30d=_to_float(row.get("roi_30d") or row.get("roi")),
            roi_90d=_to_float(row.get("roi_90d")),
            pnl_30d=_to_float(row.get("pnl_30d") or row.get("pnl")),
            drawdown=_to_float(row.get("drawdown") or row.get("max_drawdown")),
            aum=_to_float(row.get("aum") or row.get("equity")),
            followers=_to_int(row.get("followers") or row.get("follow_count")),
            win_rate=_to_float(row.get("win_rate")),
            total_trades=_to_int(row.get("total_trades")),
            copy_trade_days=_to_int(row.get("copy_trade_days")),
            raw=row,
        )

    def _row_to_position(self, row: dict[str, str], collected_at: str) -> PositionSnapshot:
        return PositionSnapshot(
            collected_at=collected_at,
            platform=row.get("platform", "csv"),
            trader_id=row.get("trader_id") or row.get("uid") or row.get("id") or "",
            symbol=row.get("symbol", ""),
            side=(row.get("side") or row.get("direction") or "").lower(),
            entry_price=_to_float(row.get("entry_price")),
            mark_price=_to_float(row.get("mark_price") or row.get("price")),
            leverage=_to_float(row.get("leverage")),
            size=_to_float(row.get("size") or row.get("qty")),
            notional=_to_float(row.get("notional")),
            pnl=_to_float(row.get("pnl")),
            pnl_pct=_to_float(row.get("pnl_pct") or row.get("profit_rate")),
            raw=row,
        )


class BinanceLeaderboardProvider(CopyTradeProvider):
    name = "binance"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json",
            "clienttype": "web",
        })

    def fetch_traders(self, limit: int = 50) -> list[TraderSnapshot]:
        payload = {
            "isShared": True,
            "periodType": "MONTHLY",
            "statisticsType": "ROI",
            "tradeType": "PERPETUAL",
        }
        endpoints = [
            "https://www.binance.com/bapi/futures/v2/public/future/leaderboard/getLeaderboardRank",
            "https://www.binance.com/bapi/futures/v3/public/future/leaderboard/getLeaderboardRank",
            "https://www.binance.com/bapi/futures/v1/public/future/leaderboard/getLeaderboardRank",
        ]
        last_error = None
        for endpoint in endpoints:
            try:
                resp = self.session.post(endpoint, json=payload, timeout=15)
                if resp.status_code == 404:
                    last_error = f"{endpoint} returned 404"
                    continue
                resp.raise_for_status()
                data = resp.json()
                rows = _dig_list(data, ["data", "list"]) or _dig_list(data, ["data", "rankList"])
                if rows:
                    return [self._row_to_trader(row, idx + 1) for idx, row in enumerate(rows[:limit])]
                last_error = f"{endpoint} returned no leaderboard list"
            except Exception as exc:
                last_error = str(exc)
        raise ProviderError(
            "Binance leaderboard public endpoints are unavailable/private now. "
            f"Last error: {last_error}"
        )

    def _row_to_trader(self, row: dict[str, Any], rank: int) -> TraderSnapshot:
        return TraderSnapshot(
            collected_at=utc_now_iso(),
            platform=self.name,
            trader_id=str(row.get("encryptedUid") or row.get("uid") or row.get("traderId") or ""),
            nickname=str(row.get("nickName") or row.get("nickname") or ""),
            rank=rank,
            roi_30d=_to_float(row.get("roi") or row.get("roiValue")),
            pnl_30d=_to_float(row.get("pnl") or row.get("pnlValue")),
            followers=_to_int(row.get("followerCount") or row.get("followers")),
            raw=row,
        )


class BitgetProvider(CopyTradeProvider):
    name = "bitget"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json",
            "locale": "en-US",
        })

    def fetch_traders(self, limit: int = 50) -> list[TraderSnapshot]:
        attempts = [
            (
                "https://api.bitget.com/api/v2/copy/mix-trader/trader-list",
                {"sortRule": "composite", "sortFlag": "desc", "pageNo": 1, "pageSize": limit},
            ),
            (
                "https://api.bitget.com/api/mix/v1/trace/traderList",
                {"sortRule": "composite", "sortFlag": "desc", "pageNo": 1, "pageSize": limit},
            ),
        ]
        last_error = None
        for endpoint, params in attempts:
            try:
                resp = self.session.get(endpoint, params=params, timeout=15)
                if resp.status_code >= 400:
                    last_error = f"{endpoint} returned {resp.status_code}: {resp.text[:160]}"
                    continue
                data = resp.json()
                rows = (
                    _dig_list(data, ["data", "resultList"])
                    or _dig_list(data, ["data", "list"])
                    or _dig_list(data, ["data"])
                )
                if rows:
                    return [self._row_to_trader(row, idx + 1) for idx, row in enumerate(rows[:limit])]
                last_error = f"{endpoint} returned no trader list"
            except Exception as exc:
                last_error = str(exc)
        raise ProviderError(
            "Bitget copy-trade public endpoint is unavailable or requires auth. "
            f"Last error: {last_error}"
        )

    def _row_to_trader(self, row: dict[str, Any], rank: int) -> TraderSnapshot:
        columns = _columns_to_map(row.get("columnList") or [])
        return TraderSnapshot(
            collected_at=utc_now_iso(),
            platform=self.name,
            trader_id=str(row.get("traderUid") or row.get("traderId") or row.get("uid") or ""),
            nickname=str(row.get("traderNickName") or row.get("nickName") or row.get("nickname") or ""),
            rank=rank,
            roi_30d=_to_float(columns.get("roi") or row.get("roi") or row.get("dailyProfitRate")),
            drawdown=_to_float(row.get("maxCallbackRate")),
            followers=_to_int(row.get("totalFollowers") or row.get("followCount")),
            win_rate=_to_float(row.get("averageWinRate") or row.get("winRate")),
            total_trades=_to_int(row.get("totalTradeCount")),
            copy_trade_days=_to_int(row.get("copyTradeDays")),
            raw=row,
        )


class PolymarketProvider(CopyTradeProvider):
    name = "polymarket"

    def __init__(
        self,
        category: str = "CRYPTO",
        time_period: str = "MONTH",
        order_by: str = "PNL",
        size_threshold: float = 1,
    ):
        self.category = category
        self.time_period = time_period
        self.order_by = order_by
        self.size_threshold = size_threshold
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0"})

    def fetch_traders(self, limit: int = 50) -> list[TraderSnapshot]:
        url = "https://data-api.polymarket.com/v1/leaderboard"
        params = {
            "category": self.category,
            "timePeriod": self.time_period,
            "orderBy": self.order_by,
            "limit": min(limit, 50),
            "offset": 0,
        }
        try:
            resp = self.session.get(url, params=params, timeout=20)
            resp.raise_for_status()
            rows = resp.json()
        except Exception as exc:
            raise ProviderError(f"Polymarket leaderboard API failed: {exc}") from exc
        if not isinstance(rows, list):
            raise ProviderError(f"Polymarket leaderboard returned unexpected payload: {type(rows)!r}")
        return [self._row_to_trader(row, idx + 1) for idx, row in enumerate(rows[:limit])]

    def fetch_positions(self, trader_id: str) -> list[PositionSnapshot]:
        url = "https://data-api.polymarket.com/positions"
        params = {
            "user": trader_id,
            "sizeThreshold": self.size_threshold,
            "limit": 500,
            "offset": 0,
            "sortBy": "CURRENT",
            "sortDirection": "DESC",
        }
        try:
            resp = self.session.get(url, params=params, timeout=20)
            resp.raise_for_status()
            rows = resp.json()
        except Exception as exc:
            raise ProviderError(f"Polymarket positions API failed for {trader_id}: {exc}") from exc
        if not isinstance(rows, list):
            return []
        return [self._row_to_position(row, trader_id) for row in rows]

    def _row_to_trader(self, row: dict[str, Any], rank: int) -> TraderSnapshot:
        wallet = str(row.get("proxyWallet") or row.get("user") or "")
        return TraderSnapshot(
            collected_at=utc_now_iso(),
            platform=self.name,
            trader_id=wallet,
            nickname=str(row.get("userName") or row.get("name") or wallet[:10]),
            rank=_to_int(row.get("rank")) or rank,
            pnl_30d=_to_float(row.get("pnl")),
            aum=_to_float(row.get("vol")),
            raw=row,
        )


class HyperliquidProvider(CopyTradeProvider):
    name = "hyperliquid"

    def __init__(self, wallets_csv: str | None = None):
        self.wallets_csv = wallets_csv
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"})

    def fetch_traders(self, limit: int = 50) -> list[TraderSnapshot]:
        if not self.wallets_csv:
            raise ProviderError("Hyperliquid needs --wallets-csv with wallet,trader_id,nickname columns")
        rows = _read_csv(self.wallets_csv)[:limit]
        out = []
        for idx, row in enumerate(rows):
            wallet = row.get("wallet") or row.get("trader_id") or row.get("address") or row.get("user")
            if not wallet:
                continue
            out.append(TraderSnapshot(
                collected_at=utc_now_iso(),
                platform=self.name,
                trader_id=wallet,
                nickname=row.get("nickname", wallet[:10]),
                rank=_to_int(row.get("rank")) or idx + 1,
                roi_30d=_to_float(row.get("roi_30d")),
                pnl_30d=_to_float(row.get("pnl_30d") or row.get("pnl")),
                drawdown=_to_float(row.get("drawdown")),
                aum=_to_float(row.get("aum") or row.get("account_value")),
                followers=_to_int(row.get("followers")),
                win_rate=_to_float(row.get("win_rate")),
                raw=row,
            ))
        return out

    def fetch_positions(self, trader_id: str) -> list[PositionSnapshot]:
        url = "https://api.hyperliquid.xyz/info"
        payload = {"type": "clearinghouseState", "user": trader_id}
        try:
            resp = self.session.post(url, json=payload, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise ProviderError(f"Hyperliquid clearinghouseState failed for {trader_id}: {exc}") from exc

        rows = data.get("assetPositions") or []
        positions = []
        for row in rows:
            pos = row.get("position") or {}
            size = _to_float(pos.get("szi"))
            if not size:
                continue
            positions.append(PositionSnapshot(
                collected_at=utc_now_iso(),
                platform=self.name,
                trader_id=trader_id,
                symbol=str(pos.get("coin") or ""),
                side="long" if size > 0 else "short",
                entry_price=_to_float(pos.get("entryPx")),
                mark_price=None,
                leverage=_to_float((pos.get("leverage") or {}).get("value") if isinstance(pos.get("leverage"), dict) else pos.get("leverage")),
                size=abs(size),
                notional=_to_float(pos.get("positionValue")),
                pnl=_to_float(pos.get("unrealizedPnl")),
                pnl_pct=_to_float(pos.get("returnOnEquity")),
                raw=row,
            ))
        return positions

    def _row_to_position(self, row: dict[str, Any], trader_id: str) -> PositionSnapshot:
        title = str(row.get("title") or row.get("slug") or row.get("conditionId") or "")
        outcome = str(row.get("outcome") or "")
        symbol = f"{title} | {outcome}" if outcome else title
        return PositionSnapshot(
            collected_at=utc_now_iso(),
            platform=self.name,
            trader_id=trader_id,
            symbol=symbol[:240],
            side="long",
            entry_price=_to_float(row.get("avgPrice")),
            mark_price=_to_float(row.get("curPrice")),
            size=_to_float(row.get("size")),
            notional=_to_float(row.get("currentValue") or row.get("initialValue")),
            pnl=_to_float(row.get("cashPnl") or row.get("realizedPnl")),
            pnl_pct=_to_float(row.get("percentPnl") or row.get("percentRealizedPnl")),
            raw=row,
        )


class OkxWeb3Provider(CopyTradeProvider):
    name = "okx_web3"
    RANK_BY = {
        "pnl": "1",
        "win_rate": "2",
        "tx": "3",
        "volume": "4",
        "roi": "5",
    }
    PERIODS = {
        "1d": "1",
        "7d": "2",
        "30d": "3",
    }

    def __init__(
        self,
        url: str = "https://web3.okx.com/copy-trade/leaderboard/solana",
        chain_id: str = "501",
        rank_by: str = "pnl",
        period: str = "30d",
        page_size: int = 20,
    ):
        self.url = url
        self.chain_id = str(chain_id)
        self.rank_by = self.RANK_BY.get(str(rank_by), str(rank_by))
        self.period = self.PERIODS.get(str(period), str(period))
        self.page_size = min(max(int(page_size), 1), 20)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/plain, */*",
            "Referer": url,
        })
        self._rows_by_wallet: dict[str, dict[str, Any]] = {}

    def fetch_traders(self, limit: int = 50) -> list[TraderSnapshot]:
        rows = self._fetch_api_rows(limit=limit)
        if not rows:
            rows = self._fetch_html_rows()
        if not rows:
            raise ProviderError("OKX Web3 leaderboard returned no wallet rows")

        self._rows_by_wallet = {
            str(row.get("walletAddress")): row
            for row in rows
            if row.get("walletAddress")
        }
        return [self._row_to_trader(row, idx + 1) for idx, row in enumerate(rows[:limit])]

    def _fetch_api_rows(self, limit: int) -> list[dict[str, Any]]:
        endpoint = "https://web3.okx.com/priapi/v1/dx/market/v2/smartmoney/ranking/content"
        rows: list[dict[str, Any]] = []
        total_count = None
        for start in range(0, limit, self.page_size):
            end = start + self.page_size
            params = {
                "chainId": self.chain_id,
                "rankStart": start,
                "rankEnd": end,
                "periodType": self.period,
                "rankBy": self.rank_by,
                "label": "all",
                "desc": "true",
            }
            try:
                resp = self.session.get(endpoint, params=params, timeout=25)
                resp.raise_for_status()
                payload = resp.json()
            except Exception:
                return []
            if payload.get("code") != 0:
                return []
            data = payload.get("data") or {}
            page_rows = data.get("rankingInfos") or []
            if not isinstance(page_rows, list) or not page_rows:
                break
            total_count = _to_int(data.get("totalCount")) or total_count
            rows.extend(page_rows)
            if total_count is not None and end >= total_count:
                break
            if len(page_rows) < self.page_size:
                break
        return _dedupe_rows(rows, key="walletAddress")[:limit]

    def _fetch_html_rows(self) -> list[dict[str, Any]]:
        try:
            resp = self.session.get(self.url, timeout=25)
            resp.raise_for_status()
        except Exception as exc:
            raise ProviderError(f"OKX Web3 leaderboard failed: {exc}") from exc

        rows = self._extract_leaderboard_rows(resp.text)
        return _dedupe_rows(rows, key="walletAddress")

    def fetch_positions(self, trader_id: str) -> list[PositionSnapshot]:
        row = self._rows_by_wallet.get(trader_id)
        if not row:
            return []
        positions = []
        for token in row.get("topTokens") or []:
            symbol = str(token.get("tokenSymbol") or token.get("tokenAddress") or "").strip()
            if not symbol:
                continue
            positions.append(PositionSnapshot(
                collected_at=utc_now_iso(),
                platform=self.name,
                trader_id=trader_id,
                symbol=symbol,
                side="long",
                pnl=_to_float(token.get("pnl")),
                pnl_pct=_to_float(token.get("roi")),
                raw={
                    **token,
                    "walletAddress": trader_id,
                    "walletName": row.get("walletName"),
                    "source": "okx_web3_topTokens",
                },
            ))
        return positions

    def _row_to_trader(self, row: dict[str, Any], rank: int) -> TraderSnapshot:
        wallet = str(row.get("walletAddress") or "")
        nickname = str(row.get("walletName") or row.get("addressAlias") or wallet[:10])
        return TraderSnapshot(
            collected_at=utc_now_iso(),
            platform=self.name,
            trader_id=wallet,
            nickname=nickname,
            rank=rank,
            roi_30d=_to_float(row.get("roi")),
            pnl_30d=_to_float(row.get("pnl")),
            aum=_to_float(row.get("volume")),
            win_rate=_to_float(row.get("winRate")),
            total_trades=_to_int(row.get("tx")),
            raw={
                **row,
                "source_url": self.url,
                "rank_by": self.rank_by,
                "period": self.period,
                "profile_url": self._profile_url(wallet, row.get("chainId")),
            },
        )

    def _extract_leaderboard_rows(self, html: str) -> list[dict[str, Any]]:
        rows = []
        seen = set()
        for match in re.finditer(r'\{"addressAlias"', html):
            obj = _extract_json_object(html, match.start())
            if not obj:
                continue
            try:
                row = json.loads(obj)
            except json.JSONDecodeError:
                continue
            wallet = row.get("walletAddress")
            if not wallet or wallet in seen:
                continue
            seen.add(wallet)
            rows.append(row)
        return rows

    def _profile_url(self, wallet: str, chain_id: Any) -> str:
        if not wallet:
            return ""
        chain = chain_id or 501
        return f"https://web3.okx.com/portfolio/{wallet}/analysis?chainIndex={chain}"


def make_provider(
    name: str,
    traders_csv: str | None = None,
    positions_csv: str | None = None,
    **kwargs,
) -> CopyTradeProvider:
    if name == "csv":
        return CsvProvider(traders_csv=traders_csv, positions_csv=positions_csv)
    if name == "binance":
        return BinanceLeaderboardProvider()
    if name == "bitget":
        return BitgetProvider()
    if name == "polymarket":
        return PolymarketProvider(
            category=kwargs.get("category", "CRYPTO"),
            time_period=kwargs.get("time_period", "MONTH"),
            order_by=kwargs.get("order_by", "PNL"),
            size_threshold=kwargs.get("size_threshold", 1),
        )
    if name == "hyperliquid":
        return HyperliquidProvider(wallets_csv=kwargs.get("wallets_csv") or traders_csv)
    if name == "okx_web3":
        return OkxWeb3Provider(
            url=kwargs.get("okx_url") or "https://web3.okx.com/copy-trade/leaderboard/solana",
            chain_id=kwargs.get("okx_chain_id", "501"),
            rank_by=kwargs.get("okx_rank_by", "pnl"),
            period=kwargs.get("okx_period", "30d"),
        )
    raise ValueError(f"Unknown provider: {name}")


def _read_csv(path: str) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _dig_list(data: Any, path: list[str]) -> list[dict[str, Any]]:
    cur = data
    for key in path:
        if not isinstance(cur, dict):
            return []
        cur = cur.get(key)
    return cur if isinstance(cur, list) else []


def _columns_to_map(columns: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for column in columns:
        key = str(column.get("describe") or column.get("key") or "").strip().lower()
        if key:
            out[key] = column.get("value")
    return out


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


def _extract_json_object(text: str, start: int) -> str | None:
    depth = 0
    in_string = False
    escaped = False
    for idx in range(start, len(text)):
        char = text[idx]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:idx + 1]
    return None


def _dedupe_rows(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    out = []
    seen = set()
    for row in rows:
        value = row.get(key)
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(row)
    return out
