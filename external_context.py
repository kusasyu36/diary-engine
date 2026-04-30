"""外部コンテキストローダー — 天気・ニュース・キャンパス情報の注入

Park et al. (2023) Generative Agents の「環境」に相当する仕組み。
エージェントの外側にある世界の情報を、日替わりで日記生成に注入する。

設計原則:
- コードは「読み込みと注入」の仕組みだけを持つ
- データは YAML ファイルに分離（ハードコードしない）
- 本番運用では YAML の代わりに天気 API (OpenWeatherMap) や
  ニュース API (NewsAPI) に差し替え可能
- エージェントはこの外部情報を「参考」として受け取るが、
  日記にどう反映するかは LLM の自律判断

評価項目 #21「外部情報の取り込み方」に対応。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

# YAML が利用可能か確認（なければフォールバック）
try:
    import yaml
    _HAS_YAML = True
except ImportError:
    import json
    _HAS_YAML = False


def load_daily_context(
    context_file: Path,
    day_num: int,
) -> Optional[str]:
    """指定された day_num に対応する外部コンテキストを YAML/JSON から読み込み、
    LLM に渡す用の自然言語テキストとして返す。

    見つからなければ None を返す（外部コンテキストなしで日記生成が進む）。
    """
    if not context_file.exists():
        return None

    text = context_file.read_text(encoding="utf-8")

    if _HAS_YAML:
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)

    if not data or "days" not in data:
        return None

    # day_num に対応するエントリを探す
    day_entry = None
    for entry in data["days"]:
        if entry.get("day") == day_num:
            day_entry = entry
            break

    if not day_entry:
        return None

    # 自然言語テキストを組み立てる
    # day/date 以外の任意キーを自然言語に整形して注入する（キャラクター非依存）
    parts = ["【今日の外部情報（参考。日記に自然に織り込んでもよいし、無視してもよい）】"]

    # キーと表示ラベルの対応（既知のキーに日本語ラベルを付ける）
    _LABELS = {
        "weather": "天気",
        "news_headline": "今日のニュース",
        "campus_event": "キャンパスの出来事",
        "nursery_event": "保育園の出来事",
        "notable": "特記事項",
        "tech_news": "テック業界のニュース",
        "industry": "業界の動き",
        "school": "学校の出来事",
        "ai_topic": "AIの話題",
        "diplomacy": "外交",
        "domestic": "国内政治",
    }

    # day, date 以外の全フィールドを走査
    skip_keys = {"day", "date"}
    for key, value in day_entry.items():
        if key in skip_keys or not value:
            continue
        label = _LABELS.get(key, key)
        parts.append(f"{label}: {value}")

    # 外部情報が何もなければ None
    if len(parts) <= 1:
        return None

    return "\n".join(parts)
