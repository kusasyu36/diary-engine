"""1日分の日記生成エンジン (5ステップ)。
キャラクター非依存。CharacterConfig と target_real_date を受け取り、
Storage / Reflection / EmotionState / LifeState を更新して Markdown を1本出力する。

5ステップ:
  ① 日記生成 (system: persona + memory + reflection_angles + emotion + life_state + ext_context)
  ② 感情 appraisal (Croissant 2024 Chain-of-Emotion 風、自然言語)
  ③ 内省の角度を自律決定 (15字以内のラベル)
  ④ 内省本文を生成 (100字以内)
  ⑤ life_state の自己更新 (節目があった時のみ。なければ NO_UPDATE)

Phase 1 の方針:
- in-world 日付 = life_state.start_date + (day_num - 1) で計算
- target_real_date は『どの実日付の出力フォルダに書くか』だけに使う
  (output/daily/YYYY-MM-DD/{character}.md)
- 同じ day_num が既に Storage / Reflection / EmotionState 全てに揃っていれば skip
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from characters import CharacterConfig
from life_state import LifeState
from live_context import fetch_for_character
from llm_client import call_llm
from memory import Reflection, Storage
from state import EmotionState

WEEKDAY_JA = ["月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日"]
INTER_STEP_SLEEP_SEC = 15  # Gemini 2.5-flash-lite の RPM 制限対策


@dataclass
class DayResult:
    character_id: str
    day_num: int
    in_world_date: str
    diary: str
    emotion: str
    angle: str
    reflection: str
    life_update_applied: list[str]
    output_path: Path
    skipped: bool = False


# ─── プロンプト組み立て ──────────────────────────────────────

def _build_system_prompt(
    config: CharacterConfig,
    storage: Storage,
    reflection_store: Reflection,
    emotion_state: EmotionState,
    life: LifeState,
    day_num: int,
    target_real_date: date,
    base_dir: Path,
) -> str:
    """persona (不変) → 記憶 → 内省角度履歴 → 感情 → life_state (現在の状態) → 外部 context (ライブ)"""
    memory_block = storage.get_recent_context(n=3, snippet_chars=200)

    past_angles = reflection_store.get_past_angles()
    angles_block = ""
    if past_angles:
        angles_block = (
            "【これまでの内省でカバーした角度（参考。あなたの視線の軌跡）】\n"
            + "\n".join(f"- {i+1}: {a}" for i, a in enumerate(past_angles))
        )

    emotion_block = emotion_state.get_context()
    life_block = life.to_prompt_block(day_num)
    ext_block = fetch_for_character(config.id, target_real_date, base_dir)

    return "\n\n".join(filter(None, [
        config.persona_const,
        memory_block,
        angles_block,
        emotion_block,
        life_block,
        ext_block,
    ]))


def _format_in_world(d: date) -> tuple[str, str]:
    weekday = WEEKDAY_JA[d.weekday()]
    return f"{d.year}年{d.month}月{d.day}日", weekday


# ─── ステップ実装 ────────────────────────────────────────────

def _generate_diary(
    config: CharacterConfig,
    system_prompt: str,
    day_num: int,
    in_world_date_str: str,
    weekday: str,
) -> str:
    user_prompt = f"""今日は{in_world_date_str}（{weekday}）、Day {day_num} です。

{config.display_name} の日記を約400字で書いてください。

{config.direction_instruction}

【共通のルール】
- 「明日も頑張ろう」「頑張るしかない」のような定型的な締めは避ける。
- 日付と曜日を最初に書いてから、本文を続けてください。
- 『現在の状態』に書かれた最新情報（年齢・所属・進路など）を必ず尊重し、
  冒頭の persona に書かれた古い情報とは矛盾させない。"""

    return call_llm(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.85,
        repetition_control=0.4,
        model="gemini-2.5-flash",
        fallback_model="gemini-2.5-flash-lite",
    )


_EMOTION_PARSE = re.compile(
    r"内面[::]\s*(.+?)(?:\Z|\n\n|変化[::]|$)", re.DOTALL | re.MULTILINE
)


def _parse_appraisal(text: str) -> str:
    m = _EMOTION_PARSE.search(text)
    if m:
        body = m.group(1).strip()
        body = body.split("\n変化:")[0].strip()
        return body
    return text.strip()


def _appraise(
    config: CharacterConfig,
    emotion_state: EmotionState,
    day_num: int,
    diary_text: str,
) -> str:
    past_openings = emotion_state.get_past_openings(max_chars=20)
    if past_openings:
        openings_block = "【過去の感情記述の冒頭（同じ言葉・比喩を再利用禁止）】\n" + "\n".join(past_openings)
    else:
        openings_block = "（これが最初の日。過去の感情記述はまだない。）"

    user_prompt = f"""【Day {day_num} の日記】
===
{diary_text}
===

{openings_block}

【タスク】 2 段階で答えてください。

Step 1 「変化の因果」
- 今日の日記の中から、{config.character_label} の内面に作用した具体的な出来事・発言・場面を 1 つ特定。
- それが内面にどう作用したかを 30 字以内で書く。

Step 2 「現在の内面状態」
- Step 1 の変化を踏まえて、今の内面を 2〜3 文の自然な日本語で描写。
- 【厳守】過去の冒頭と同じ書き出しや同じ比喩は絶対に使わない。
- 【厳守】数値やスコアは使わない。今日の日記から新しい語彙を拾うこと。

出力フォーマット (この形式を厳守):
変化: <Step 1 の 30 字>
内面: <Step 2 の 2〜3 文>"""

    response = call_llm(
        system_prompt=config.persona_const,
        user_prompt=user_prompt,
        temperature=0.8,
        repetition_control=0.5,
        model="gemini-2.5-flash-lite",
        fallback_model="gemini-2.5-flash",
    )
    new_description = _parse_appraisal(response)
    emotion_state.update(day_num, new_description)
    return new_description


def _determine_angle(
    config: CharacterConfig,
    day_num: int,
    diary_text: str,
    past_angles: list[str],
) -> str:
    past_summary = "（まだない）" if not past_angles else "\n".join(f"- {a}" for a in past_angles)
    user_prompt = f"""あなたは {config.character_label} の内面を観察する視点です。

【これまでの内省でカバーしてきた角度（短いラベル）】
{past_summary}

【今日（Day {day_num}）の日記】
===
{diary_text}
===

【タスク】
今日の日記から取り出せる、過去に触れていない新しい内省の角度を、15字以内の短いラベルで1つだけ答えてください。
- 具体的で、今日ならではのもの
- 過去のラベルと意味が重ならないこと
- ラベルだけを1行で出力。前置きや説明は不要。"""

    return call_llm(
        system_prompt=config.persona_const,
        user_prompt=user_prompt,
        temperature=0.9,
        repetition_control=0.3,
        model="gemini-2.5-flash-lite",
        fallback_model="gemini-2.5-flash",
    )


def _generate_reflection(
    config: CharacterConfig,
    day_num: int,
    diary_text: str,
    angle: str,
) -> str:
    user_prompt = f"""あなたは {config.character_label}。以下が今日（Day {day_num}）の日記です。

===
{diary_text}
===

【今日の内省の角度】
{angle}

【タスク】
上の角度から、今日の日記の「下に流れる気づき」を100字以内の内省文として1つだけ書いてください。
- 日記の要約ではなく、日記では書ききれなかったメタな自己観察
- 100字以内で1〜2文
- 定型表現や、どの日にも通用する抽象文は避ける
- 前置きや見出しは不要、本文だけ"""

    return call_llm(
        system_prompt=config.persona_const,
        user_prompt=user_prompt,
        temperature=0.85,
        repetition_control=0.5,
        model="gemini-2.5-flash-lite",
        fallback_model="gemini-2.5-flash",
    )


def _maybe_update_life(
    config: CharacterConfig,
    life: LifeState,
    day_num: int,
    diary_text: str,
    reflection_text: str,
) -> list[str]:
    """life_state の自己更新ステップ。
    LLM に『今日の日記と内省を踏まえて状態を更新するか？』を聞く。
    重要な節目 (進路決定・人間関係の変化・新しい関心事の出現・年齢進行) のみ更新。
    """
    current_state = life.to_prompt_block(day_num)
    user_prompt = f"""あなたは {config.character_label} の長期的な変化を記録する観察者です。

【現在保持している「現在の状態」】
{current_state}

【今日の日記】
===
{diary_text}
===

【今日の内省】
{reflection_text}

【タスク】
今日の日記と内省を踏まえて、上記の「現在の状態」を更新する必要があるかどうかを判定してください。

更新する基準:
- 進路の決定や明示的な選択 (例: 進振りの結果が確定した、転職を決めた)
- 人間関係の決定的変化 (例: 親友と仲直り、決別、新しい出会い、誰かが亡くなった)
- 新しい関心事が日記の中で繰り返し出現し、定着しつつある
- 年齢が物理的に変わった (誕生日)
- 重大な出来事 (引っ越し、卒業、入院、発見) があった

更新しない基準:
- 一日限りの感情の波 (それは emotion_state が拾う)
- 反復的な日常の出来事
- 単なる思いつきや一時的な気分

出力フォーマット (どちらか必ず一つを選んで返答):

[A] 更新が必要な場合:
UPDATE: {{"fields": {{...}}, "life_events": [{{"label": "...", "summary": "..."}}], "milestones": [{{"label": "...", "status": "done"}}]}}

  - fields は浅いマージで適用される (career_intent や occupation を上書きするときに使う)。
  - relationships を更新するときは fields.relationships の中に追加したい人物だけ書く。
  - current_concerns を更新したい場合はリストごと上書き (新しい関心事リスト全文)。
  - life_events は今日の節目を追記する (label と summary を簡潔に)。
  - 既存の milestone を完了にする場合は label を一致させ status を done にする。

[B] 更新が不要な場合:
NO_UPDATE

(JSON 部分のみを単独行で出力。前置きや解説は禁止。)"""

    response = call_llm(
        system_prompt=config.persona_const,
        user_prompt=user_prompt,
        temperature=0.4,
        repetition_control=0.0,
        model="gemini-2.5-flash-lite",
        fallback_model="gemini-2.5-flash",
    )
    diff = life.parse_update_response(response)
    if diff is None:
        return []
    return life.apply_update(diff, day_num)


# ─── エントリ ───────────────────────────────────────────────

def run_one_day(
    config: CharacterConfig,
    target_real_date: date,
    *,
    base_dir: Optional[Path] = None,
    sleep_sec: int = INTER_STEP_SLEEP_SEC,
    verbose: bool = True,
) -> DayResult:
    """指定キャラクターの『次の1日分』を生成して保存。
    target_real_date は出力先のフォルダ名 (output/daily/YYYY-MM-DD/) に使う。
    in-world の日付は life_state から計算される。
    """
    base_dir = base_dir or Path(__file__).parent

    storage = Storage(config.storage_path)
    reflection_store = Reflection(config.reflection_path)
    emotion_state = EmotionState(
        config.state_path,
        initial_emotion=config.initial_emotion,
        character_label=config.character_label,
    )
    life = LifeState(config.life_path)

    day_num = life.next_day_num()
    in_world = life.in_world_date(day_num)
    in_world_date_str, weekday = _format_in_world(in_world)

    daily_dir = base_dir / "output" / "daily" / target_real_date.isoformat()
    daily_dir.mkdir(parents=True, exist_ok=True)
    out_path = daily_dir / f"{config.id}.md"

    # idempotent skip: 全層に同じ day_num が既にあれば再実行しない
    if (storage.has_day(day_num)
            and reflection_store.has_day(day_num)
            and emotion_state.has_day(day_num)
            and out_path.exists() and out_path.stat().st_size > 0):
        if verbose:
            print(f"  [{config.id}] Day {day_num} は既に生成済み → skip")
        return DayResult(
            character_id=config.id, day_num=day_num,
            in_world_date=in_world.isoformat(),
            diary="", emotion="", angle="", reflection="",
            life_update_applied=[], output_path=out_path, skipped=True,
        )

    if verbose:
        print(f"  [{config.id}] Day {day_num} ({in_world.isoformat()} {weekday})")

    # ① 日記生成
    system_prompt = _build_system_prompt(
        config, storage, reflection_store, emotion_state, life, day_num,
        target_real_date, base_dir,
    )
    if verbose:
        print(f"    ① 日記生成中...")
    diary = _generate_diary(config, system_prompt, day_num, in_world_date_str, weekday)
    storage.append_day(day_num, in_world_date_str, weekday, diary)
    if verbose:
        print(f"       → 保存 ({len(diary)}字)")
    time.sleep(sleep_sec)

    # ② 感情 appraisal
    if verbose:
        print(f"    ② 感情 appraisal...")
    new_emotion = _appraise(config, emotion_state, day_num, diary)
    if verbose:
        print(f"       → {new_emotion[:50]}...")
    time.sleep(sleep_sec)

    # ③ 内省角度
    if verbose:
        print(f"    ③ 内省角度を決定中...")
    past_angles = reflection_store.get_past_angles()
    angle = _determine_angle(config, day_num, diary, past_angles).strip()
    if verbose:
        print(f"       → 「{angle}」")
    time.sleep(sleep_sec)

    # ④ 内省本文
    if verbose:
        print(f"    ④ 内省文生成中...")
    reflection_text = _generate_reflection(config, day_num, diary, angle).strip()
    reflection_store.append(day_num, reflection_text, angle=angle)
    if verbose:
        print(f"       → ({len(reflection_text)}字)")
    time.sleep(sleep_sec)

    # ⑤ life_state 自己更新
    if verbose:
        print(f"    ⑤ life_state 自己更新を判定中...")
    applied = _maybe_update_life(config, life, day_num, diary, reflection_text)
    if verbose:
        if applied:
            print(f"       → 更新: {', '.join(applied)}")
        else:
            print(f"       → NO_UPDATE")

    # day_num を進める (skip 判定の終端)
    life.advance(day_num)

    # Markdown 出力
    md = (
        f"# {config.display_name} — Day {day_num}\n\n"
        f"- 物語内日付: {in_world_date_str}（{weekday}）\n"
        f"- 実カレンダー: {target_real_date.isoformat()}\n\n"
        f"## 日記\n\n{diary.strip()}\n\n"
        f"## 内省（角度: {angle}）\n\n{reflection_text.strip()}\n"
    )
    if applied:
        md += f"\n## life_state 更新\n\n- {', '.join(applied)}\n"
    out_path.write_text(md, encoding="utf-8")

    return DayResult(
        character_id=config.id,
        day_num=day_num,
        in_world_date=in_world.isoformat(),
        diary=diary,
        emotion=new_emotion,
        angle=angle,
        reflection=reflection_text,
        life_update_applied=applied,
        output_path=out_path,
        skipped=False,
    )
