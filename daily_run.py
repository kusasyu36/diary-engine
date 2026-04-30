"""毎日12時の自動実行用エントリーポイント。

使い方:
  python daily_run.py                              # 今日の日付・全員
  python daily_run.py --date 2026-04-30            # 指定日付・全員
  python daily_run.py --character ren              # 蓮だけ
  python daily_run.py --character ren engineer     # 複数指定
  python daily_run.py --dry-run                    # API を叩かず、対象と day_num だけ表示

出力:
  output/daily/YYYY-MM-DD/{character}.md
  state/{character}_life.json (必要時のみ更新)
  output/daily_{character}_day_logs.json (毎日 append)
  output/daily_{character}_reflections.json (毎日 append)
  output/daily_{character}_state.json (毎日更新)

エラーハンドリング:
- 1人で失敗しても他のキャラの処理は続行する。
- 終了時に成功/失敗のサマリを表示。
- 失敗キャラがあれば exit code 1。
"""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import date
from pathlib import Path

from characters import CHARACTERS, all_ids, get
from daily_pipeline import run_one_day
from life_state import LifeState


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="5キャラの1日分の日記を生成する")
    p.add_argument(
        "--date",
        type=lambda s: date.fromisoformat(s),
        default=date.today(),
        help="出力フォルダ名 (実カレンダー日付)。default=today",
    )
    p.add_argument(
        "--character",
        nargs="+",
        choices=all_ids() + ["all"],
        default=["all"],
        help="生成対象。default=all",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="API を叩かず、対象と次の day_num を表示するだけ",
    )
    p.add_argument(
        "--no-sleep",
        action="store_true",
        help="ステップ間の sleep を 0 にする（テスト用）",
    )
    return p.parse_args()


def resolve_targets(selection: list[str]) -> list[str]:
    if "all" in selection:
        return all_ids()
    seen = set()
    out: list[str] = []
    for s in selection:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def main() -> int:
    args = parse_args()
    targets = resolve_targets(args.character)

    print("=" * 60)
    print(f"daily_run: 実カレンダー = {args.date.isoformat()}")
    print(f"対象キャラ: {targets}")
    print("=" * 60)

    if args.dry_run:
        for cid in targets:
            cfg = get(cid)
            try:
                life = LifeState(cfg.life_path)
                next_day = life.next_day_num()
                in_world = life.in_world_date(next_day).isoformat()
                print(f"  [{cid}] next day_num={next_day}, in_world={in_world}")
            except FileNotFoundError as e:
                print(f"  [{cid}] life_state がない: {e}")
        return 0

    sleep_sec = 0 if args.no_sleep else 15

    successes: list[str] = []
    failures: list[tuple[str, str]] = []
    skipped: list[str] = []

    for cid in targets:
        cfg = get(cid)
        try:
            result = run_one_day(
                cfg,
                target_real_date=args.date,
                sleep_sec=sleep_sec,
                verbose=True,
            )
            if result.skipped:
                skipped.append(cid)
            else:
                successes.append(cid)
                print(f"  ✓ [{cid}] Day {result.day_num} → {result.output_path}")
        except Exception as e:
            failures.append((cid, str(e)))
            print(f"  ✗ [{cid}] 失敗: {e}", file=sys.stderr)
            traceback.print_exc()

    print("=" * 60)
    print(f"成功: {len(successes)} / 失敗: {len(failures)} / skip: {len(skipped)}")
    if successes:
        print(f"  成功キャラ: {successes}")
    if skipped:
        print(f"  skip キャラ: {skipped}")
    if failures:
        print(f"  失敗キャラ:")
        for cid, msg in failures:
            print(f"    - {cid}: {msg}")
    print("=" * 60)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
