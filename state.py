"""感情状態層 (Phase 7) — Croissant 2024 Chain-of-Emotion に着想を得た実装

Croissant et al. (2024) "An Appraisal-Based Chain-of-Emotion Architecture for
Affective Language Model Game Agents" のアプローチに着想を得ている。

Croissant 論文のアプローチ:
- LLM がテキストベースであることを活かし、感情を自然言語で扱う
- OCC 型の決定木ではなく、LLM に状況と人格設定から感情を推論させる
- appraisal プロンプト: "Briefly describe how Chibitea feels right now given
  the situation and their personality. Describe why they feel a certain way.
  Chibitea feels:"

本実装での適用:
- valence/arousal/anxiety のような数値変数は持たない（Croissant 論文と同様に
  自然言語で感情を保持する実用的選択）
- 「褒められると不安」のような特殊ルールは persona.py にのみ書かれ、
  appraisal プロンプトでは参照されない (LLM が system prompt の人格設定から
  自律的に読み取って判断する)
- 作業記憶を 2〜4 文の自然言語として実装（多くのエージェントシステムで
  トークンレベルの作業記憶が使われているという CoALA サーベイ (2025) の知見を参考）

Sources:
- Croissant et al. (2024) PLoS ONE: https://pmc.ncbi.nlm.nih.gov/articles/PMC11086867/
- Hu et al. (2025) Memory in the Age of AI Agents: arXiv 2512.13564
"""
from __future__ import annotations

import json
from pathlib import Path


# デフォルト初期感情（キャラクター固有の初期感情は各 run スクリプトで initial_emotion 引数で指定する）。
# initial_emotion を渡し忘れた場合のフォールバック。特定キャラに依存しない中立的な状態。
INITIAL_EMOTION = "特筆すべき感情の変化はまだない。これが最初の日。"


class EmotionState:
    """自然言語の感情状態を JSON ファイルで永続化する Working Memory。

    - 現在の感情: 2〜4 文の自然言語
    - 履歴: 各 Day の感情記述を保存
    - 更新: LLM の appraisal 応答をそのまま保存 (数値変換なし)
    """

    def __init__(self, path: Path, initial_emotion: str | None = None, character_label: str = "キャラクター"):
        self.path = Path(path)
        self._initial_emotion = initial_emotion or INITIAL_EMOTION
        self._character_label = character_label
        self.data = self._load()

    def _load(self) -> dict:
        if self.path.exists() and self.path.stat().st_size > 0:
            return json.loads(self.path.read_text(encoding="utf-8"))
        return {
            "current": self._initial_emotion,
            "history": [],   # [{day: int, description: str}]
        }

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def current(self) -> str:
        """現在の感情状態 (自然言語文字列) を返す。"""
        return self.data["current"]

    def update(self, day_num: int, new_description: str):
        """LLM が appraisal で生成した新しい感情記述で状態を更新する。
        数値への変換も閾値マッピングも一切行わない。LLM の自然言語をそのまま持つ。
        同じ day_num の history エントリが既にあれば置換 (resume 時の重複防止)。
        """
        self.data["current"] = new_description
        self.data["history"] = [h for h in self.data["history"] if h["day"] != day_num]
        self.data["history"].append({
            "day": day_num,
            "description": new_description,
        })
        self.data["history"].sort(key=lambda h: h["day"])
        self.save()

    def has_day(self, day_num: int) -> bool:
        return any(h["day"] == day_num for h in self.data["history"])

    def get_past_openings(self, max_chars: int = 20) -> list[str]:
        """これまでの感情記述の「冒頭 max_chars 文字」だけを抜き出して返す。
        Anthropic Context Engineering (2025) と NexusSum 階層要約 (ACL 2025) の発想:
        生テキストを渡すと LLM が模倣する (self-reinforcement cascade)。
        抽象化した痕跡だけを渡せば、模倣経路を物理的に遮断できる。
        """
        openings = []
        for h in self.data["history"]:
            desc = h.get("description", "")
            opening = desc[:max_chars].replace("\n", " ")
            openings.append(f"Day {h['day']}: {opening}...")
        return openings

    def get_context(self) -> str:
        """日記生成時に persona + memory に付け加える Working Memory コンテキスト。
        Croissant (2024) と同様に、自然言語をそのまま渡す。
        """
        return (
            f"【今の{self._character_label}の内面（文体に自然に滲ませる参考）】\n"
            + self.data["current"]
        )

    def reset(self):
        self.data = {
            "current": self._initial_emotion,
            "history": [],
        }
        self.save()
