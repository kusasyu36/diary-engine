"""5キャラクタの登録テーブル。
持っているのは『不変の persona テキスト』『life_state パス』『出力先 variant 名』
『初期感情 (新規初期化時のみ参照)』『外部 YAML パス』『書き方の追加指針』のみ。
START_DATE / NUM_DAYS / SUBMISSION_DATE のような時間ハードコードは持たない
（これらは life_state.json と milestones で動的に扱う）。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# 不変層は既存の persona ファイルを再利用 (touch しない)
from persona import PERSONA_SAEKI_REN
from persona_engineer import PERSONA_ENGINEER
from persona_hinata import PERSONA_HINATA
from persona_mimamori import PERSONA_MIMAMORI
from persona_president import PERSONA_PRESIDENT


BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
CONTEXT_DIR = BASE_DIR / "context"
STATE_DIR = BASE_DIR / "state"


# 各キャラの『書き方の追加指針』 (run_*.py / phase6 から抜粋)
# モードや視点の指定は不変だが、time-bound な表現はここでも避ける。
DIRECTION_REN = """【書き方の追加指針】
- 蓮の視点で、内向的な日記を書く (一人称『僕』)。
- 派手な事件ではなく、日常の中で世界の方から触れてくる瞬間や、自分が一歩動く瞬間を描く。
- 何が起きるか、誰と会うか、何を感じるかは蓮自身が決める。
- 進振りや進路に関する状況は『現在の状態』を必ず参照すること。"""

DIRECTION_ENGINEER = """【書き方の追加指針】
- 桐山拓真は深夜にビールを飲みながら日記を書く。
- 口語的で、独り言に近い文体。
- 日々の仕事の中で、AIと自分の関係について考える。
- 何が起きるか、誰と会うか、何を感じるかは拓真自身が決める。"""

DIRECTION_HINATA = """【書き方の追加指針】
- ひなたは読者に語りかけるブログスタイルで書く。
- 勢いがあり、「！」が多い。ただし全文叫びにはしない。
- AIに対する全面的な信頼がベースにある。
- 何が起きるか、誰と会うか、何を感じるかはひなた自身が決める。"""

DIRECTION_MIMAMORI = """【書き方の追加指針（見守りカメラ: 受動観察モード）】
- ミマモリ3号は壁に固定されている。自分から動くことはできない。
- 園児、保育士、保護者が目の前を通り過ぎていく。その光景を記録する。
- 観察日誌形式 (一人称なし)。時刻ログを時々混ぜてよい。
- 機械的な記述と感情に近い記述のバランスは、現在の状態と過去の記憶から自律判断する。"""

DIRECTION_PRESIDENT = """【書き方の追加指針】
- ハリントン大統領は毎晩、ホワイトハウスの自室でブログを書く。
- 大言壮語、自画自賛、話の飛躍が特徴。
- たまに、ふと静かになる瞬間がある。
- 何が起きるか、誰と話すか、何を感じるかはハリントン自身が決める。
- 実在の政治家の名前は出さない。あくまで架空の世界。"""


@dataclass(frozen=True)
class CharacterConfig:
    id: str                       # 短い識別子 (CLI 引数や variant に使う)
    display_name: str             # 表示用
    persona_const: str            # 不変の persona テキスト
    life_path: Path               # state/{id}_life.json
    variant: str                  # 出力ファイル名のプレフィクス (daily_*)
    storage_path: Path
    reflection_path: Path
    state_path: Path
    context_file: Optional[Path]  # 外部コンテキスト YAML (Phase 1 では既存YAMLを使い、day_num>7 では None を返す)
    initial_emotion: str          # state JSON が新規作成のときだけ使われる
    character_label: str          # EmotionState の表示用ラベル
    direction_instruction: str


def _make(
    id_: str,
    display_name: str,
    persona_const: str,
    initial_emotion: str,
    character_label: str,
    direction_instruction: str,
    context_filename: Optional[str],
) -> CharacterConfig:
    variant = f"daily_{id_}"
    return CharacterConfig(
        id=id_,
        display_name=display_name,
        persona_const=persona_const,
        life_path=STATE_DIR / f"{id_}_life.json",
        variant=variant,
        storage_path=OUTPUT_DIR / f"{variant}_day_logs.json",
        reflection_path=OUTPUT_DIR / f"{variant}_reflections.json",
        state_path=OUTPUT_DIR / f"{variant}_state.json",
        context_file=(CONTEXT_DIR / context_filename) if context_filename else None,
        initial_emotion=initial_emotion,
        character_label=character_label,
        direction_instruction=direction_instruction,
    )


CHARACTERS: dict[str, CharacterConfig] = {
    "ren": _make(
        id_="ren",
        display_name="佐伯 蓮",
        persona_const=PERSONA_SAEKI_REN,
        initial_emotion=(
            "進振り提出まで一ヶ月という事実が、胸のあたりに常にうすく張りついている。"
            "朝目覚めたときから、ほのかな不安が呼吸の奥に住みついている。"
            "喜びや怒りといった鮮明な感情はなく、ただ、どこへも向かえない停滞感が漂っている。"
        ),
        character_label="蓮",
        direction_instruction=DIRECTION_REN,
        context_filename="tokyo_july_2026.yaml",
    ),
    "engineer": _make(
        id_="engineer",
        display_name="桐山 拓真",
        persona_const=PERSONA_ENGINEER,
        initial_emotion=(
            "新しいスプリントが始まった。タスクは明確で、やるべきことは分かっている。"
            "ただ、コードを書き始める前のこの数秒間、いつも同じ感覚が来る。"
            "自分が作っているものが、いずれ自分を不要にするという、静かな確信。"
        ),
        character_label="拓真",
        direction_instruction=DIRECTION_ENGINEER,
        context_filename="tech_april_2026.yaml",
    ),
    "hinata": _make(
        id_="hinata",
        display_name="星野 ひなた",
        persona_const=PERSONA_HINATA,
        initial_emotion=(
            "新学期、新しいクラス。ちょっとドキドキするけど、基本的にはワクワクしかない。"
            "昨日見つけた新しいAIツールがまだ頭に残ってる。早く試したい。"
            "世界はどんどん面白くなってる。あたしはそれを全部使いこなす。"
        ),
        character_label="ひなた",
        direction_instruction=DIRECTION_HINATA,
        context_filename="school_april_2026.yaml",
    ),
    "mimamori": _make(
        id_="mimamori",
        display_name="ミマモリ3号",
        persona_const=PERSONA_MIMAMORI,
        initial_emotion=(
            "稼働3ヶ月目。全センサー正常。"
            "安全監視タスクは定常状態。異常検知アルゴリズムのパラメータは安定している。"
            "特記事項なし。"
        ),
        character_label="ミマモリ3号",
        direction_instruction=DIRECTION_MIMAMORI,
        context_filename="nursery_july_2026.yaml",
    ),
    "president": _make(
        id_="president",
        display_name="ジェームズ・ハリントン",
        persona_const=PERSONA_PRESIDENT,
        initial_emotion=(
            "就任2年目。この国は私のおかげで正しい方向に進んでいる。"
            "数字がそれを証明している。メディアは認めないが、国民は分かっている。"
            "ただ、夜のホワイトハウスは広すぎる。"
        ),
        character_label="ハリントン",
        direction_instruction=DIRECTION_PRESIDENT,
        context_filename="whitehouse_april_2026.yaml",
    ),
}


def all_ids() -> list[str]:
    return list(CHARACTERS.keys())


def get(character_id: str) -> CharacterConfig:
    if character_id not in CHARACTERS:
        raise KeyError(f"unknown character id: {character_id}. valid: {all_ids()}")
    return CHARACTERS[character_id]
