"""ライブ外部情報取得モジュール (Phase 2)。

旧: external_context.py が日付固定の YAML を引いてくる仕組みだったのを、
新: 実カレンダー日付の RSS / 天気 をライブで取得する仕組みに切り替える。

キャラごとの news_sources.py の設定に従って、RSS フィードからその日の見出しを
取り出し、Open-Meteo (無料・キー不要) から天気を取り出す。LLM には『参考情報、
無視してもよい』として注入する (既存 external_context.py の方針を踏襲)。

キャッシュ: 1日1回しか取りに行かない (output/cache/news_YYYY-MM-DD.json) ので、
同じ日に複数キャラを生成しても RSS サーバには1セットしかリクエストしない。
"""
from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any, Optional

import certifi
import feedparser

from news_sources import NewsSourceConfig, get_source

# macOS の Python.org ビルドはシステム CA を持たないため、certifi を明示的に使う。
# feedparser は内部で urllib を使うので、グローバルな SSL コンテキストを certifi に向ける。
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())
ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())

CACHE_DIR_NAME = "output/cache"
RSS_TIMEOUT_SEC = 10
WEATHER_TIMEOUT_SEC = 8

# Open-Meteo 用の都市座標 (キーなしで使える)
WEATHER_LOCATIONS = {
    "Tokyo":      (35.6762, 139.6503, "Asia/Tokyo"),
    "Washington": (38.9072, -77.0369, "America/New_York"),
}


# ─── キャッシュ ───
def _cache_path(base_dir: Path, target_date: date) -> Path:
    return base_dir / CACHE_DIR_NAME / f"news_{target_date.isoformat()}.json"


def _load_cache(base_dir: Path, target_date: date) -> dict | None:
    p = _cache_path(base_dir, target_date)
    if p.exists() and p.stat().st_size > 0:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
    return None


def _save_cache(base_dir: Path, target_date: date, payload: dict) -> None:
    p = _cache_path(base_dir, target_date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ─── RSS 取得 ───
def _fetch_rss_items(feed_url: str) -> list[dict[str, str]]:
    """単一フィードから新着 entry を { title, link, summary } で返す。失敗時は空。"""
    try:
        # feedparser は urllib を内部で使う。タイムアウトは socket レベルで制御。
        import socket
        socket.setdefaulttimeout(RSS_TIMEOUT_SEC)
        feed = feedparser.parse(feed_url)
    except Exception:
        return []

    items: list[dict[str, str]] = []
    for entry in feed.entries[:20]:  # 各フィード最大20件
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        summary = (entry.get("summary") or entry.get("description") or "").strip()
        # HTMLタグを乱暴に除去
        import re
        summary = re.sub(r"<[^>]+>", "", summary)
        summary = summary.replace("\n", " ").strip()[:200]
        items.append({
            "title": title,
            "link": entry.get("link", ""),
            "summary": summary,
            "source": feed.feed.get("title", feed_url),
        })
    return items


def _filter_items(
    items: list[dict[str, str]],
    config: NewsSourceConfig,
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in items:
        title = item.get("title", "")
        if config.keyword_excludes and any(ex in title for ex in config.keyword_excludes):
            continue
        if config.keyword_filters:
            if not any(kw in title for kw in config.keyword_filters):
                continue
        out.append(item)
        if len(out) >= config.max_items:
            break
    return out


# ─── 天気取得 ───
def _fetch_weather(location: str, target_date: date) -> Optional[str]:
    """Open-Meteo から target_date の天気を取って自然言語で返す。失敗時は None。"""
    if location not in WEATHER_LOCATIONS:
        return None
    lat, lon, tz = WEATHER_LOCATIONS[location]
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&daily=temperature_2m_max,temperature_2m_min,weathercode,precipitation_sum"
        f"&timezone={urllib.parse.quote(tz)}"
        f"&start_date={target_date.isoformat()}&end_date={target_date.isoformat()}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "diary-engine/1.0"})
        with urllib.request.urlopen(req, timeout=WEATHER_TIMEOUT_SEC, context=_SSL_CTX) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None

    daily = data.get("daily") or {}
    try:
        tmax = daily["temperature_2m_max"][0]
        tmin = daily["temperature_2m_min"][0]
        code = daily["weathercode"][0]
        precip = daily["precipitation_sum"][0]
    except (KeyError, IndexError, TypeError):
        return None

    weather_text = _wmo_to_ja(code)
    parts = [f"{location} {weather_text}", f"最高{tmax:.0f}℃ / 最低{tmin:.0f}℃"]
    if precip and precip > 0.5:
        parts.append(f"降水量 {precip:.0f}mm")
    return "、".join(parts)


def _wmo_to_ja(code: int) -> str:
    """Open-Meteo の WMO コードを荒く日本語化する。"""
    table = {
        0: "快晴", 1: "晴れ", 2: "晴れ時々曇り", 3: "曇り",
        45: "霧", 48: "霧 (着氷)",
        51: "霧雨 (弱)", 53: "霧雨", 55: "霧雨 (強)",
        61: "雨 (弱)", 63: "雨", 65: "雨 (強)",
        71: "雪 (弱)", 73: "雪", 75: "雪 (強)",
        80: "にわか雨 (弱)", 81: "にわか雨", 82: "にわか雨 (強)",
        95: "雷雨", 96: "雷雨と雹", 99: "激しい雷雨と雹",
    }
    return table.get(int(code), "天候不明")


# ─── 統合: キャッシュ込みでキャラ用コンテキストを返す ───

def fetch_for_character(
    character_id: str,
    target_date: date,
    base_dir: Path,
) -> Optional[str]:
    """指定キャラの『今日の参考情報』を自然言語ブロックで返す。
    キャッシュ (output/cache/news_YYYY-MM-DD.json) を1日単位で利用。
    取れる情報がなければ None (LLM には何も注入しない)。
    """
    cache = _load_cache(base_dir, target_date) or {}
    char_cache = cache.get(character_id)

    if char_cache is None:
        config = get_source(character_id)
        # RSS
        all_items: list[dict[str, str]] = []
        for url in config.rss_feeds:
            all_items.extend(_fetch_rss_items(url))
        filtered = _filter_items(all_items, config)
        # 天気
        weather: Optional[str] = None
        if config.weather_location:
            weather = _fetch_weather(config.weather_location, target_date)
        char_cache = {
            "items": filtered,
            "weather": weather,
        }
        cache[character_id] = char_cache
        _save_cache(base_dir, target_date, cache)

    return _format_block(char_cache)


def _format_block(char_cache: dict[str, Any]) -> Optional[str]:
    items = char_cache.get("items") or []
    weather = char_cache.get("weather")
    if not items and not weather:
        return None

    lines = ["【今日の外部情報（参考。日記に自然に織り込んでもよいし、無視してもよい）】"]
    if weather:
        lines.append(f"天気: {weather}")
    if items:
        lines.append("最近のニュース見出し:")
        for it in items:
            t = it.get("title", "").strip()
            s = it.get("summary", "").strip()
            src = it.get("source", "").strip()
            tail = f"（{src}）" if src else ""
            if s and len(s) > 80:
                s = s[:80] + "…"
            lines.append(f"- {t}{tail}")
            if s:
                lines.append(f"  {s}")
    return "\n".join(lines)
