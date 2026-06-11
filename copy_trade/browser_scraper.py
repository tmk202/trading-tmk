from __future__ import annotations

import json
import os
import re
import requests
import subprocess
import tempfile
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from copy_trade.models import TraderSnapshot, utc_now_iso


DEFAULT_CHROME_PATHS = [
    os.getenv("CLOAK_BROWSER_PATH", ""),
    os.getenv("CHROME_PATH", ""),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
]


@dataclass
class BrowserResult:
    url: str
    html: str
    browser_path: str
    returncode: int


class BrowserScrapeError(RuntimeError):
    pass


def fetch_static_page(url: str, timeout: int = 30) -> BrowserResult:
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
    resp.raise_for_status()
    return BrowserResult(url=url, html=resp.text, browser_path="requests", returncode=0)


class ChromeDumpBrowser:
    def __init__(
        self,
        browser_path: str | None = None,
        user_data_dir: str | None = None,
        headless: bool = True,
        timeout: int = 45,
    ):
        self.browser_path = browser_path or _find_browser()
        self.user_data_dir = user_data_dir
        self.headless = headless
        self.timeout = timeout
        if not self.browser_path:
            raise BrowserScrapeError(
                "No Chrome/Cloak browser binary found. Set CLOAK_BROWSER_PATH or CHROME_PATH."
            )

    def dump_dom(self, url: str, wait_ms: int = 8000) -> BrowserResult:
        tmp_profile = None
        user_data_dir = self.user_data_dir
        if not user_data_dir:
            tmp_profile = tempfile.TemporaryDirectory(prefix="copy-trade-browser-")
            user_data_dir = tmp_profile.name

        cmd = [
            self.browser_path,
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-http2",
            "--disable-background-networking",
            "--disable-sync",
            "--disable-extensions",
            "--disable-blink-features=AutomationControlled",
            "--ignore-certificate-errors",
            "--window-size=1440,1200",
            f"--user-data-dir={user_data_dir}",
            f"--virtual-time-budget={wait_ms}",
            "--dump-dom",
            url,
        ]
        if self.headless:
            cmd.insert(1, "--headless=new")

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout or ""
            if isinstance(partial, bytes):
                partial = partial.decode("utf-8", errors="replace")
            if partial:
                return BrowserResult(url=url, html=partial, browser_path=self.browser_path, returncode=124)
            raise BrowserScrapeError(f"Browser timed out loading {url}") from exc
        finally:
            if tmp_profile is not None:
                tmp_profile.cleanup()

        html = proc.stdout or ""
        if proc.returncode != 0 and not html:
            raise BrowserScrapeError((proc.stderr or "Browser failed").strip())
        return BrowserResult(url=url, html=html, browser_path=self.browser_path, returncode=proc.returncode)


def discover_page(html: str) -> dict[str, Any]:
    scripts = _extract_script_text(html)
    urls = sorted(set(_extract_urls(html)))
    api_urls = [
        url for url in urls
        if any(key in url.lower() for key in ["api", "bapi", "copy", "leader", "trader", "trace"])
    ]
    next_data = _extract_next_data(scripts)
    text = _visible_text(html)
    return {
        "title": _extract_title(html),
        "api_urls": api_urls,
        "all_urls": urls,
        "next_data_keys": sorted(next_data.keys()) if isinstance(next_data, dict) else [],
        "text_sample": text[:2000],
        "html_bytes": len(html.encode("utf-8")),
    }


def discover_page_with_assets(url: str, html: str, max_assets: int = 20) -> dict[str, Any]:
    discovery = discover_page(html)
    asset_urls = _extract_asset_urls(url, html)
    asset_findings = []
    all_api_urls = set(discovery["api_urls"])
    for asset_url in asset_urls[:max_assets]:
        try:
            resp = requests.get(asset_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            if resp.status_code >= 400:
                continue
            text = resp.text
        except Exception:
            continue
        urls = _extract_urls(text)
        api_urls = [
            item for item in urls
            if any(key in item.lower() for key in ["api", "bapi", "copy", "leader", "trader", "trace"])
        ]
        for item in api_urls:
            all_api_urls.add(item)
        string_hits = _extract_interesting_strings(text)
        asset_findings.append({
            "asset": asset_url,
            "bytes": len(text.encode("utf-8")),
            "api_urls": api_urls[:100],
            "strings": string_hits[:100],
        })
    discovery["asset_urls"] = asset_urls
    discovery["asset_findings"] = asset_findings
    discovery["api_urls"] = sorted(all_api_urls)
    return discovery


def parse_generic_traders(html: str, platform: str = "browser") -> list[TraderSnapshot]:
    """Best-effort parser for pages with embedded JSON trader cards.

    This is intentionally conservative. It only emits rows when a JSON object
    contains trader-like identifiers plus at least one performance metric.
    """
    scripts = _extract_script_text(html)
    candidates: list[dict[str, Any]] = []
    for script in scripts:
        candidates.extend(_json_objects_from_text(script))

    traders = []
    seen = set()
    for obj in _walk_dicts(candidates):
        trader_id = (
            obj.get("traderUid")
            or obj.get("traderId")
            or obj.get("encryptedUid")
            or obj.get("uid")
            or obj.get("userId")
        )
        roi = obj.get("roi") or obj.get("roi30d") or obj.get("profitRate") or obj.get("dailyProfitRate")
        nickname = obj.get("traderNickName") or obj.get("nickName") or obj.get("nickname") or obj.get("userName")
        if not trader_id or roi is None:
            continue
        key = (platform, str(trader_id))
        if key in seen:
            continue
        seen.add(key)
        traders.append(TraderSnapshot(
            collected_at=utc_now_iso(),
            platform=platform,
            trader_id=str(trader_id),
            nickname=str(nickname or ""),
            rank=len(traders) + 1,
            roi_30d=_to_float(roi),
            pnl_30d=_to_float(obj.get("pnl") or obj.get("profit") or obj.get("totalpl")),
            drawdown=_to_float(obj.get("drawdown") or obj.get("maxCallbackRate")),
            followers=_to_int(obj.get("followers") or obj.get("followCount") or obj.get("totalFollowers")),
            win_rate=_to_float(obj.get("winRate") or obj.get("averageWinRate")),
            total_trades=_to_int(obj.get("totalTradeCount") or obj.get("tradingOrders")),
            copy_trade_days=_to_int(obj.get("copyTradeDays")),
            raw=obj,
        ))
    return traders


def save_browser_artifacts(out_dir: str, slug: str, html: str, discovery: dict[str, Any]) -> tuple[str, str]:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    safe_slug = re.sub(r"[^a-zA-Z0-9_.-]+", "_", slug).strip("_") or "page"
    html_path = os.path.join(out_dir, f"{safe_slug}.html")
    json_path = os.path.join(out_dir, f"{safe_slug}.discover.json")
    with open(html_path, "w", encoding="utf-8") as handle:
        handle.write(html)
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(discovery, handle, ensure_ascii=False, indent=2, sort_keys=True)
    return html_path, json_path


def _find_browser() -> str:
    for path in DEFAULT_CHROME_PATHS:
        if path and os.path.exists(path):
            return path
    return ""


def _extract_urls(html: str) -> list[str]:
    patterns = [
        r'https?://[^"\'<>)\\ ]+',
        r'/(?:api|bapi|copy|copyTrading|copy-trading|leaderboard|leader-board|trace)[^"\'<>)\\ ]+',
    ]
    urls = []
    for pattern in patterns:
        urls.extend(re.findall(pattern, html, flags=re.I))
    return [unescape(url).replace("\\u002F", "/") for url in urls]


def _extract_asset_urls(page_url: str, html: str) -> list[str]:
    from urllib.parse import urljoin

    raw = re.findall(r'(?:src|href)=["\']([^"\']+\.(?:js|json)(?:\?[^"\']*)?)["\']', html, flags=re.I)
    return sorted({urljoin(page_url, unescape(item).replace("\\u002F", "/")) for item in raw})


def _extract_interesting_strings(text: str) -> list[str]:
    hits = []
    for match in re.finditer(r'["\']([^"\']{4,220})["\']', text):
        value = match.group(1)
        low = value.lower()
        if any(key in low for key in ["copy", "leader", "trader", "ranking", "position", "pnl", "roi", "aum"]):
            hits.append(value)
    return sorted(set(hits))


def _extract_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
    return unescape(re.sub(r"\s+", " ", match.group(1)).strip()) if match else ""


def _extract_next_data(scripts: list[str]) -> Any:
    for script in scripts:
        stripped = script.strip()
        if stripped.startswith("{") and ("props" in stripped or "pageProps" in stripped):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass
    return {}


def _extract_script_text(html: str) -> list[str]:
    return re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.I | re.S)


def _json_objects_from_text(text: str) -> list[Any]:
    out = []
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            out.append(json.loads(stripped))
            return out
        except json.JSONDecodeError:
            pass

    for match in re.finditer(r"(\{[^{}]{20,4000}\})", text):
        raw = match.group(1)
        if not any(key in raw for key in ["trader", "Trader", "roi", "ROI", "nick", "Nick"]):
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


def _walk_dicts(values: list[Any]):
    stack = list(values)
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            yield item
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip = False

    def handle_starttag(self, tag, attrs):
        if tag.lower() in ("script", "style", "noscript"):
            self.skip = True

    def handle_endtag(self, tag):
        if tag.lower() in ("script", "style", "noscript"):
            self.skip = False

    def handle_data(self, data):
        if not self.skip:
            cleaned = " ".join(data.split())
            if cleaned:
                self.parts.append(cleaned)


def _visible_text(html: str) -> str:
    parser = _TextParser()
    parser.feed(html)
    return " ".join(parser.parts)


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
