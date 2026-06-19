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
from datetime import date, timedelta
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
    """persona (不変) → 長期アーク → 記憶 → 内省角度履歴 → 感情 → life_state (現在の状態) → 外部 context (ライブ)"""
    arc_block = _build_arc_block(life, day_num)
    memory_block = storage.get_recent_context(n=5, snippet_chars=260)

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
        arc_block,
        memory_block,
        angles_block,
        emotion_block,
        life_block,
        ext_block,
    ]))


def _build_arc_block(life: LifeState, day_num: int) -> str:
    """長期アーク: 経過日数の重みと、これまでの節目(life_events)の全履歴を見せる。
    『直近3日の鏡』しか見えていなかった従来の弱点を補い、"もう何日もここにいる"
    という時間の蓄積を本人に意識させる。"""
    events = life.data.get("life_events") or []
    lines = [
        "【あなたの長期的な軌跡（俯瞰）】",
        f"- あなたは今 Day {day_num}。物語が始まってから {day_num - 1} 日が経過した。",
        "  これは『昨日の続き』ではなく、もう何十日もこの生を生きてきたということ。",
        "  同じ場所で足踏みしているなら、それ自体に本人が気づき、倦み、痺れを切らしてよい。",
    ]
    if events:
        lines.append("- これまでに起きた節目（古い順）:")
        for e in events:
            d = e.get("in_world_date", "?")
            lines.append(f"  - Day {e.get('day_num','?')} ({d}): {e.get('label','')} — {e.get('summary','')}")
    else:
        lines.append("- まだ『節目』と呼べる出来事は一度も起きていない。"
                     "それは静かな日々の証でもあるが、何かが動いてよい頃合いでもある。")
    return "\n".join(lines)


def _format_in_world(d: date) -> tuple[str, str]:
    weekday = WEEKDAY_JA[d.weekday()]
    return f"{d.year}年{d.month}月{d.day}日", weekday


# ─── ステップ実装 ────────────────────────────────────────────

def _build_change_directive(
    life: LifeState,
    storage: Storage,
    day_num: int,
) -> str:
    """この日記で『昨日と違うこと』を必ず起こさせるための指示を組み立てる。
    - 状況の反復禁止（過去の書き出しを具体的に列挙して封じる）
    - 超過している節目があれば、今日その決着に一歩踏み込ませる
    これが従来エンジンに欠けていた最大の弁。語彙の反復ではなく、状況の反復を断つ。"""
    lines = ["【今日、物語を前に進めるための厳守ルール】",
             "- 今日は『昨日までと状況が動く』ことを最低1つ必ず書く。",
             "  次のいずれかを具体的に: ①小さくても決断する ②行動を起こす ③誰かと具体的にやり取りする"
             " ④予想外の出来事が起きる ⑤環境や関係が一段変わる。",
             "- 心情の反芻・同じ風景・同じ嘆きの言い換えだけで終わらせない。"
             "『考えた』で終えず、『何かが起きた／動いた』を書く。"]

    # 直近の日記の書き出しを禁止（状況の反復を封じる）
    recent = storage.data.get("days", [])[-6:]
    openings = []
    for d in recent:
        head = (d.get("diary", "") or "").strip().replace("\n", " ")[:40]
        if head:
            openings.append(f"  - Day {d.get('day')}: 「{head}…」")
    if openings:
        lines.append("- 過去に使った書き出し・状況をなぞらない（下記と同じ入り方を禁止）:")
        lines.extend(openings)

    # 超過した節目があれば決着を促す
    today = life.in_world_date(day_num)
    for m in (life.data.get("milestones") or []):
        if m.get("status", "pending") != "pending":
            continue
        try:
            diff = (date.fromisoformat(m["date"]) - today).days
        except (KeyError, ValueError):
            continue
        if diff < 0:
            lines.append(
                f"- 【最重要】節目「{m.get('label','')}」は既に {-diff} 日超過し、未決着のまま放置されている。"
                "これ以上『過ぎてしまった』と嘆くだけの描写は禁止。今日はこの件に対して"
                "具体的な動き（あきらめて別の道を選ぶ／誰かに打ち明ける／代替の一手を打つ／締切の事後対応をする 等）を"
                "起こし、状況を一段進めること。")
        elif diff == 0:
            lines.append(f"- 節目「{m.get('label','')}」は本日。今日その結末（決断・行動・結果）を必ず描く。")

    return "\n".join(lines)


def _generate_diary(
    config: CharacterConfig,
    system_prompt: str,
    day_num: int,
    in_world_date_str: str,
    weekday: str,
    change_directive: str,
) -> str:
    user_prompt = f"""今日は{in_world_date_str}（{weekday}）、Day {day_num} です。

{config.display_name} の日記を約400字で書いてください。

{config.direction_instruction}

{change_directive}

【共通のルール】
- すべて日本語で書く。地の文に英単語（no, yes, my own 等）を混ぜない。
- 「明日も頑張ろう」「頑張るしかない」のような定型的な締めは避ける。
- 日付と曜日を最初に書いてから、本文を続けてください。
- 『現在の状態』に書かれた最新情報（年齢・所属・進路など）を必ず尊重し、
  冒頭の persona に書かれた古い情報とは矛盾させない。
- 抽象的な問い（「どこへ向かうのだろう」等）で締めるのを避け、
  今日起きた具体的な変化の余韻で終える。"""

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
- すべて日本語で書く。英単語（no, yes, my own 等）を混ぜない
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
- 小さな一歩でも、それが今後の行動・関係・関心に継続的な影響を残すなら記録してよい
  (例: 初めて親に本音を漏らした、ある人との関係が一段深まった/壊れた、新しい習慣を始めた)

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
  - 【重要】milestones は『日付つきの具体的な目標』だけに使う。日々の関心・気づき・感想は
    milestones ではなく life_events に書くこと（日付のない milestone は無視される）。
  - 既存の milestone を完了にする場合は label を一致させ status を done にする。
    ただし、まだ期日が来ていない目標を勝手に done にしない。

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


def _ensure_forward_goal(
    config: CharacterConfig,
    life: LifeState,
    day_num: int,
    diary_text: str,
) -> list[str]:
    """前を向く目標（pending milestone）が一つも無ければ、新しい目標を立てる。

    停滞の深層原因は『目標が達成/喪失された後、次の行き先が生成されないこと』だった。
    人は目標を一つ終えれば次の目標を持つ。それを欠くと "賽の河原" になる。
    pending が一つでもあれば何もしない（既に行き先がある）。
    """
    milestones = life.data.get("milestones") or []
    # 日付つきで未完了(pending/in_progress)の目標が一つでもあれば、既に行き先がある。
    if any(m.get("date") and m.get("status", "pending") in ("pending", "in_progress")
           for m in milestones):
        return []

    today = life.in_world_date(day_num)
    done_labels = [m.get("label", "") for m in milestones if m.get("status") == "done"]
    current_state = life.to_prompt_block(day_num)
    user_prompt = f"""あなたは {config.character_label} の人生の『次の行き先』を見立てる観察者です。

【現在の状態】
{current_state}

【今日の日記】
===
{diary_text}
===

【状況】
{config.character_label} は今、明確な目標（次の節目）を持っていません。
これまでに区切りがついた節目: {', '.join(done_labels) if done_labels else 'なし'}

【タスク】
今日の日記と現在の状態から自然に芽生える、{config.character_label} 自身の「次の目標・気がかり・向かおうとしている先」を1つだけ立ててください。
- 大げさな人生目標でなくてよい。数週間〜数ヶ月先に具体的な区切りが来るもの。
- 今日の日記で芽生えた動き（関心・関係・行動）の延長線上にあること。
- 本人がまだ無自覚でも、向かいつつある方向を言語化する。

出力フォーマット (JSON 単独行のみ。前置き禁止):
{{"label": "<10〜20字の目標ラベル>", "horizon_days": <今日から何日後に区切りが来るか。14〜90の整数>, "concern": "<その目標に伴う今の気がかりを20字程度で>"}}"""

    response = call_llm(
        system_prompt=config.persona_const,
        user_prompt=user_prompt,
        temperature=0.7,
        repetition_control=0.0,
        model="gemini-2.5-flash-lite",
        fallback_model="gemini-2.5-flash",
    )
    import json as _json
    chunk = None
    for c in re.findall(r"\{[\s\S]*?\}", response):
        try:
            chunk = _json.loads(c)
            break
        except _json.JSONDecodeError:
            continue
    if not chunk or not chunk.get("label"):
        return []

    try:
        horizon = int(chunk.get("horizon_days", 30))
    except (TypeError, ValueError):
        horizon = 30
    horizon = max(14, min(90, horizon))
    target = today + timedelta(days=horizon)

    life.data.setdefault("milestones", []).append({
        "date": target.isoformat(),
        "label": chunk["label"],
        "status": "pending",
    })
    concern = (chunk.get("concern") or "").strip()
    if concern:
        concerns = life.data.setdefault("fields", {}).setdefault("current_concerns", [])
        if concern not in concerns:
            concerns.append(concern)
    life.save()
    return [f"new_goal:{chunk['label']}({target.isoformat()})"]


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
    change_directive = _build_change_directive(life, storage, day_num)
    diary = _generate_diary(
        config, system_prompt, day_num, in_world_date_str, weekday, change_directive
    )
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

    # ⑤b 行き先（pending milestone）が空なら次の目標を自動生成する
    new_goal = _ensure_forward_goal(config, life, day_num, diary)
    if new_goal:
        applied += new_goal
        if verbose:
            print(f"       → 次の目標を生成: {', '.join(new_goal)}")
    time.sleep(sleep_sec)

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
    # 注: 旧版は内部状態タグ「## life_state 更新 — milestones(+N)」を本文に出力していた。
    # これが最大の「AI臭」と指摘されたため、本文からは除去（applied はログ・戻り値にのみ残す）。
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
