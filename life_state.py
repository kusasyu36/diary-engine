"""可変層: キャラクターの「現在の状態」を JSON で永続化する。

不変の persona (性格・癖・文体・核心の価値観) と切り離し、時間と共に変化する
情報 (年齢・所属・人間関係・関心事・節目イベント) を別ファイルで管理する。

設計意図:
- persona ファイルにハードコードされた「20歳、東大2年生、進振り提出まで1ヶ月」のような
  時間依存記述は、長期運用すると毎日システムプロンプトで「時間が止まったまま」を伝え続け、
  LLM の記憶 (reflection / experience) と矛盾する。
- 本モジュールは「現在の状態」を記憶層と並走させ、AI 自身に更新させる。
- 進振り後に蓮が法学部を選ぶか、リーガルテックに進むかは life_state の更新で表現される。

スキーマ (state/{character}_life.json):
{
  "id": "ren",
  "display_name": "佐伯 蓮",
  "start_date": "2026-07-06",          # Day 1 の物語内日付 (不変)
  "current_day_num": 7,                 # 直近に書き終えた day_num
  "fields": {                            # 現在の状態 (時間と共に変化)
    "age": 20,
    "occupation": "東京大学 文科一類 2年",
    "career_intent": "弁護士志望",
    "location": "東京",
    "household": "両親と妹と同居",
    "relationships": { "父": "...", ... },
    "current_concerns": ["..."],
    "notes": ""
  },
  "milestones": [                        # 物語の節目 (確定 / 予定)
    {"date": "2026-08-05", "label": "進振り提出締切", "status": "pending"}
  ],
  "life_events": [                       # 過去に起きた節目イベント (AIが追記)
    {"day_num": 12, "in_world_date": "2026-07-17", "label": "進振りで法学部を選択", "summary": "..."}
  ]
}
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any


class LifeState:
    """キャラクターの可変層を扱う。Storage / Reflection と同じ append-only 系列。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(
                f"life_state ファイルが見つかりません: {self.path}\n"
                f"初回は state/ に手動で作成するか、初期化スクリプトで作ってください。"
            )
        self.data = json.loads(self.path.read_text(encoding="utf-8"))

    # ─── 永続化 ───
    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ─── 日付・Day番号 ───
    @property
    def start_date(self) -> date:
        return date.fromisoformat(self.data["start_date"])

    @property
    def current_day_num(self) -> int:
        return int(self.data.get("current_day_num", 0))

    def next_day_num(self) -> int:
        return self.current_day_num + 1

    def in_world_date(self, day_num: int) -> date:
        return self.start_date + timedelta(days=day_num - 1)

    def advance(self, day_num: int) -> None:
        """1日生成し終えた後、current_day_num を更新する。"""
        if day_num > self.current_day_num:
            self.data["current_day_num"] = day_num
            self.save()

    # ─── プロンプトブロック ───
    def to_prompt_block(self, day_num: int) -> str:
        """system prompt の最後に追加する『現在の状態』ブロック。
        persona に書かれた時間依存記述 (年齢・所属・残り日数等) と矛盾する場合、
        必ずこちらを優先することを明示する。
        """
        f = self.data["fields"]
        in_world = self.in_world_date(day_num).isoformat()

        lines = [
            "【現在の状態（最新・必ず優先）】",
            "※ 冒頭の persona に記述された時間依存情報（年齢・所属・残り日数・職業など）と",
            "   矛盾する場合、必ず以下の『現在の状態』を優先してください。",
            f"- 物語内の今日の日付: {in_world}（Day {day_num}）",
        ]

        for key, label in [
            ("age", "年齢"),
            ("occupation", "所属・職業"),
            ("career_intent", "進路の意向"),
            ("location", "居住地"),
            ("household", "同居家族"),
        ]:
            v = f.get(key)
            if v not in (None, "", []):
                lines.append(f"- {label}: {v}")

        rel = f.get("relationships") or {}
        if rel:
            lines.append("- 主な人間関係:")
            for who, desc in rel.items():
                lines.append(f"  - {who}: {desc}")

        concerns = f.get("current_concerns") or []
        if concerns:
            lines.append("- 目下の関心事:")
            for c in concerns:
                lines.append(f"  - {c}")

        notes = f.get("notes")
        if notes:
            lines.append(f"- 補足: {notes}")

        # 節目 (今後の予定) を残り日数つきで提示
        milestones = self.data.get("milestones") or []
        pending = [m for m in milestones if m.get("status", "pending") == "pending"]
        if pending:
            lines.append("- 今後の節目:")
            today = self.in_world_date(day_num)
            for m in pending:
                try:
                    target = date.fromisoformat(m["date"])
                    diff = (target - today).days
                    if diff > 0:
                        rel_text = f"あと{diff}日"
                    elif diff == 0:
                        rel_text = "本日"
                    else:
                        rel_text = f"{-diff}日経過（未対応）"
                except (KeyError, ValueError):
                    rel_text = ""
                label = m.get("label", "")
                lines.append(f"  - {label} ({m.get('date', '?')}, {rel_text})")

        # 過去の節目 (確定済み) — 直近3つだけ
        events = self.data.get("life_events") or []
        if events:
            lines.append("- これまでの節目:")
            for e in events[-3:]:
                d = e.get("in_world_date", "?")
                label = e.get("label", "")
                lines.append(f"  - Day {e.get('day_num', '?')} ({d}): {label}")

        return "\n".join(lines)

    # ─── 自己更新 ───
    UPDATE_RE = re.compile(r"UPDATE\s*[::]\s*(\{.*\})", re.DOTALL)
    NO_UPDATE_RE = re.compile(r"NO[_\s-]*UPDATE", re.IGNORECASE)

    def parse_update_response(self, response: str) -> dict | None:
        """LLM の更新応答から JSON 差分を取り出す。
        NO_UPDATE / 解析失敗時は None。差分が見つかれば dict を返す。
        """
        if self.NO_UPDATE_RE.search(response) and not self.UPDATE_RE.search(response):
            return None
        m = self.UPDATE_RE.search(response)
        if not m:
            # フォールバック: 単独の JSON ブロックがあれば拾う
            for chunk in re.findall(r"\{[\s\S]*\}", response):
                try:
                    return json.loads(chunk)
                except json.JSONDecodeError:
                    continue
            return None
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            return None

    def apply_update(self, diff: dict, day_num: int) -> list[str]:
        """JSON merge 風に差分を適用。
        - fields は浅いマージ (キーごとに上書き)。relationships は深いマージ。
        - life_events は append。
        - milestones は status='done' に書き換える指示なら上書き、新規なら append。
        - current_concerns はリストごと置き換え (LLM の意図を尊重)。
        戻り値: 適用したフィールド名のリスト (ログ用)
        """
        applied: list[str] = []

        new_fields = diff.get("fields") or {}
        if new_fields:
            cur = self.data.setdefault("fields", {})
            for key, val in new_fields.items():
                if key == "relationships" and isinstance(val, dict):
                    rel = cur.setdefault("relationships", {})
                    rel.update(val)
                    applied.append(f"fields.relationships({len(val)})")
                else:
                    cur[key] = val
                    applied.append(f"fields.{key}")

        new_events = diff.get("life_events") or []
        if new_events:
            for e in new_events:
                e.setdefault("day_num", day_num)
                e.setdefault("in_world_date", self.in_world_date(day_num).isoformat())
                self.data.setdefault("life_events", []).append(e)
            applied.append(f"life_events(+{len(new_events)})")

        new_milestones = diff.get("milestones") or []
        if new_milestones:
            current = self.data.setdefault("milestones", [])
            for m in new_milestones:
                # 同じ label の既存があれば上書き、なければ append
                idx = next(
                    (i for i, existing in enumerate(current)
                     if existing.get("label") == m.get("label")),
                    None,
                )
                if idx is None:
                    current.append(m)
                else:
                    current[idx].update(m)
            applied.append(f"milestones(+{len(new_milestones)})")

        if applied:
            self.save()
        return applied
