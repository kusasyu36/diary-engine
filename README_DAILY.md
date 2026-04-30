# 毎日12時 自動日記システム — 運用手順

5キャラ (蓮 / 桐山 / ひなた / ミマモリ3号 / ハリントン大統領) が毎日12:00 JST に
日記を1本ずつ書き、GitHub Pages に自動公開する。

## 構成

| 役割 | 場所 |
|---|---|
| 日記生成エンジン | `daily_pipeline.py` |
| CLI | `daily_run.py` |
| 不変層 (人格) | `persona.py`, `persona_engineer.py`, ... |
| 可変層 (現在の状態) | `state/{character}_life.json` |
| 記憶層 | `output/daily_{character}_*.json` |
| 外部情報源 | `live_context.py` (RSS + Open-Meteo 天気、無料・キー不要) |
| 出力 | `output/daily/YYYY-MM-DD/{character}.md` |
| 静的サイト生成 | `publish_site.py` → `site/` |
| 自動実行 | `.github/workflows/daily.yml` |

## ローカルで手動実行 (動作確認用)

```bash
cd pattern_saeki_ren
.venv/bin/python3 daily_run.py            # 今日・全員
.venv/bin/python3 daily_run.py --dry-run  # 何も書かずに day_num だけ表示
.venv/bin/python3 daily_run.py --character ren --no-sleep   # 蓮だけ高速実行
.venv/bin/python3 publish_site.py         # site/ を再生成
```

## GitHub での自動運用 — 初回セットアップ手順

### 1. GitHub にリポジトリを作成

新規リポジトリを推奨 (例: `daily-diary-engine`)。Public でないと Pages の無料枠が使えない。

ローカルから push:

```bash
# プロジェクトルート (pattern_saeki_ren/) で
git init   # まだなら
git remote add origin https://github.com/<USER>/<REPO>.git
git add .
git commit -m "initial commit"
git push -u origin main
```

> 既存の松尾研リポジトリの一部として運用する場合は、ワークフローの `env.WORKDIR` を
> `pattern_saeki_ren` のままにすればそのまま動く (リポジトリ直下なら `WORKDIR: .` に変更)。

### 2. Secrets に Gemini API キーを登録

GitHub の Settings → Secrets and variables → Actions → "New repository secret" から以下7つを追加:

- `GEMINI_API_KEY`
- `GEMINI_API_KEY_2`
- `GEMINI_API_KEY_3`
- `GEMINI_API_KEY_4`
- `GEMINI_API_KEY_5`
- `GEMINI_API_KEY_6`
- `GEMINI_API_KEY_7`

ローカルの `.env` に書いてある値をそのまま貼ればよい。

### 3. GitHub Pages を有効化

Settings → Pages → "Build and deployment" の Source を **"GitHub Actions"** に変更。
(Branch 配信ではなく Actions 配信を選ぶ)

### 4. 初回手動実行

Actions タブ → "daily-diary" → "Run workflow" → main ブランチで実行。

成功すると:
- `output/daily/YYYY-MM-DD/` に5本の md
- `state/*_life.json` が必要に応じて更新
- `site/` が再構築されてリポジトリにコミット
- GitHub Pages にデプロイ

公開 URL: `https://<USER>.github.io/<REPO>/`

### 5. cron 確認

`.github/workflows/daily.yml` の `cron: "0 3 * * *"` は **03:00 UTC = 12:00 JST**。
GitHub Actions の cron は最大15分ほど遅延することがある (公式仕様)。

## トラブルシュート

### Quota 超過
1日に5キャラ × 5ステップ = 25回呼ぶ。Gemini 2.5 Flash 無料枠は1キーあたり250 RPD。
7キー分散しているので余裕はある。1キー枯渇すると次キーに自動フォールバックする。
全7キーが同時枯渇したら、その日はそのキャラの生成が止まる (他キャラに影響しない)。
ログは Actions の generate ステップに残る。

### 日記が同じ日付で2回書かれない
`life_state.current_day_num` の append-only 制御により、Day N が既に書かれていれば
スキップする。同じ日に再実行しても安全。

### 文章が反復・崩壊し始めた
- 過去の reflection 角度ラベルが似てきている可能性 → `output/daily_{character}_reflections.json` を確認
- emotion_state の冒頭20字が似ている可能性 → `output/daily_{character}_state.json` の history を確認
- 必要なら life_state を手で書き換えて方向転換する (新しい current_concerns を入れる等)

### キャラの状態を手動で直したい
`state/{character}_life.json` を直接編集してコミット。次の自動実行から反映される。

## ローカルテストの便利オプション

```bash
# 状態を変えずにシミュレーションだけ
.venv/bin/python3 daily_run.py --dry-run

# 1キャラだけ・sleep なしで素早く確認
.venv/bin/python3 daily_run.py --character engineer --no-sleep

# 別の日付として動かす (Day 番号は life_state から計算されるので変わらない)
.venv/bin/python3 daily_run.py --date 2026-05-01
```

## 関連ファイル一覧

- `life_state.py` — 可変層の読み書き + 自己更新の差分マージ
- `characters.py` — 5キャラの登録 (persona / 出力先 / 外部情報源など)
- `daily_pipeline.py` — 1日5ステップのエンジン
- `daily_run.py` — CLI エントリ
- `live_context.py` — RSS / 天気の取得 (キャッシュ付き)
- `news_sources.py` — キャラごとの RSS フィード設定
- `publish_site.py` — `output/daily/` を `site/` に静的サイト化
- `state/{character}_life.json` — 各キャラの可変層
- `output/daily_{character}_*.json` — 記憶層 (Storage / Reflection / EmotionState)
- `output/daily/YYYY-MM-DD/{character}.md` — 日々の生成物 (Single Source of Truth)
- `site/` — `publish_site.py` の出力 (再生成可能な派生物)
