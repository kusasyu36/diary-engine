"""記憶モジュール
Storage → Reflection → Experience の3層モデル。
Phase 5 では Storage 層だけを実装する。

Storage: 起きた日記の生記録を JSON で保存し、直近N日分を取り出す。
Park et al. (2023) の memory stream の単純版、
および From Storage to Experience (2026) の最初の段階。
"""
import json
from pathlib import Path


class Storage:
    """日記の生記録を保存・取り出しする倉庫。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = self._load()

    def _load(self) -> dict:
        if self.path.exists() and self.path.stat().st_size > 0:
            return json.loads(self.path.read_text(encoding="utf-8"))
        return {"days": []}

    def save(self):
        """ディスクに保存する。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def append_day(self, day_num: int, date_str: str, weekday: str, diary_text: str):
        """1日分を追記する。既に同じ day_num があれば置換する (resume 時の重複防止)。"""
        entry = {
            "day": day_num,
            "date": date_str,
            "weekday": weekday,
            "diary": diary_text,
        }
        # 既存を削除してから追記 (順序は Day 番号昇順を維持)
        self.data["days"] = [d for d in self.data["days"] if d["day"] != day_num]
        self.data["days"].append(entry)
        self.data["days"].sort(key=lambda d: d["day"])
        self.save()

    def has_day(self, day_num: int) -> bool:
        """指定したDay番号が既に保存されているか。"""
        return any(d["day"] == day_num for d in self.data["days"])

    def get_recent_context(self, n: int = 3, snippet_chars: int = 200) -> str:
        """直近n日分を、LLMに渡す用のテキストにフォーマットする。
        各日の冒頭 snippet_chars 文字だけを抜き出す(トークン節約)。
        Day 1 時点ではまだ記憶がないので空っぽメッセージを返す。
        """
        recent = self.data["days"][-n:]
        if not recent:
            return "（まだ日記の記憶はない。これが最初の日。）"

        lines = ["【直近のあなたの日記の記憶】"]
        for entry in recent:
            snippet = entry["diary"][:snippet_chars].replace("\n", " ")
            lines.append(
                f"\n■ Day {entry['day']} ({entry['date']} {entry['weekday']}):\n{snippet}..."
            )
        return "\n".join(lines)

    def reset(self):
        """全データを消す(Phase変更時や再実験用)。"""
        self.data = {"days": []}
        self.save()


class Reflection:
    """内省（気づき）の蓄積。
    Park et al. (2023) の reflection、および From Storage to Experience (2026) の第2段階。
    方向B（深い停滞）では「反芻と深まり」を記録する。
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = self._load()

    def _load(self) -> dict:
        if self.path.exists() and self.path.stat().st_size > 0:
            return json.loads(self.path.read_text(encoding="utf-8"))
        return {"reflections": []}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def append(self, day_num: int, text: str, angle: str = ""):
        """1日分の内省を追記する。既に同じ day_num があれば置換 (resume 時の重複防止)。
        angle: その内省がカバーする角度（次回の焦点決定で使う抽象サマリー）
        """
        entry = {
            "day": day_num,
            "angle": angle,
            "text": text,
        }
        self.data["reflections"] = [r for r in self.data["reflections"] if r["day"] != day_num]
        self.data["reflections"].append(entry)
        self.data["reflections"].sort(key=lambda r: r["day"])
        self.save()

    def get_past_angles(self) -> list[str]:
        """これまでカバーした角度の一覧を返す（生の内省文は返さない）。
        次の内省生成時に、LLM に『まだ触れていない角度』を自律判断させるための材料。
        """
        return [r.get("angle", "") for r in self.data["reflections"] if r.get("angle")]

    def has_day(self, day_num: int) -> bool:
        return any(r["day"] == day_num for r in self.data["reflections"])

    def get_recent_context(self, n: int = 3) -> str:
        """直近n日分の内省をテキストにフォーマット。"""
        recent = self.data["reflections"][-n:]
        if not recent:
            return "（まだ内省の記憶はない。）"
        lines = ["【直近のあなたの内省（気づき）】"]
        for entry in recent:
            lines.append(f"\n■ Day {entry['day']}: {entry['text']}")
        return "\n".join(lines)

    def reset(self):
        self.data = {"reflections": []}
        self.save()
