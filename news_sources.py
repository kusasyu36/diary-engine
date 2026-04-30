"""キャラクターごとの外部情報源 (RSS フィード + 天気) の定義。

設計方針:
- API キーが要らない無料源だけで構成 (RSS + Open-Meteo)。
- 各キャラの『関心事』に近いソースを並べ、キーワードフィルタで絞り込む。
- LLM への注入は『参考情報、使っても無視してもよい』として渡す。

NOTE: RSS フィードURLは時々変わる。エラー時は他のフィードに自動フォールバックするように
live_context.py 側で実装する。フィードが全部死んでも『今日のニュースは取得できなかった』として
日記生成は続行する。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class NewsSourceConfig:
    rss_feeds: tuple[str, ...] = ()         # RSS URL のリスト
    keyword_filters: tuple[str, ...] = ()    # 含む見出しだけ採用 (空なら全件)
    keyword_excludes: tuple[str, ...] = ()   # 含む見出しは除外
    max_items: int = 3                        # 最大採用件数
    weather_location: str | None = None       # Open-Meteo 用 (Tokyo / Washington 等)


# キャラごとの設定
NEWS_SOURCES: dict[str, NewsSourceConfig] = {
    # 蓮: 法律・AI・進学に関するニュース、首都圏の天気
    "ren": NewsSourceConfig(
        rss_feeds=(
            "https://www3.nhk.or.jp/rss/news/cat0.xml",       # NHK 主要ニュース
            "https://www3.nhk.or.jp/rss/news/cat6.xml",       # NHK 文化・教育
            "https://news.yahoo.co.jp/rss/categories/it.xml", # Yahoo IT
        ),
        keyword_filters=("AI", "弁護", "法律", "進学", "大学", "テック", "司法", "知財", "規制"),
        max_items=3,
        weather_location="Tokyo",
    ),

    # 桐山: AI・テック・スタートアップ
    "engineer": NewsSourceConfig(
        rss_feeds=(
            "https://techcrunch.com/feed/",
            "https://news.ycombinator.com/rss",
            "https://www.publickey1.jp/atom.xml",
        ),
        keyword_filters=(),  # フィルタなし、新着順
        keyword_excludes=("Show HN:", "Ask HN:"),
        max_items=4,
        weather_location="Tokyo",
    ),

    # ひなた: AI・若者・SNS・起業
    "hinata": NewsSourceConfig(
        rss_feeds=(
            "https://techcrunch.com/feed/",
            "https://www3.nhk.or.jp/rss/news/cat0.xml",
        ),
        keyword_filters=("AI", "GPT", "Claude", "Gemini", "起業", "SNS", "中学", "高校", "ティーン", "学生", "TikTok", "X "),
        max_items=3,
        weather_location="Tokyo",
    ),

    # ミマモリ3号: 天気のみ (ニュースは見られない設定)
    "mimamori": NewsSourceConfig(
        rss_feeds=(),
        max_items=0,
        weather_location="Tokyo",
    ),

    # ハリントン大統領: 国際政治・米国国内
    "president": NewsSourceConfig(
        rss_feeds=(
            "http://feeds.bbci.co.uk/news/world/rss.xml",
            "https://feeds.npr.org/1004/rss.xml",            # NPR World
            "https://feeds.reuters.com/Reuters/worldNews",
        ),
        keyword_filters=(),
        max_items=4,
        weather_location="Washington",
    ),
}


def get_source(character_id: str) -> NewsSourceConfig:
    return NEWS_SOURCES.get(character_id, NewsSourceConfig())
