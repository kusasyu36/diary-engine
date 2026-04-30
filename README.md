# AIキャラクターの7日間の日記生成

Bコース選考課題の実装コードです。

## 動かし方

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`.env` ファイルに Gemini API キーを設定してください。

```
GEMINI_API_KEY=your_api_key_here
```

蓮（主役）の日記を生成する場合：

```bash
python phase6_with_reflection.py --direction C --reset
```

追加キャラクターの日記を生成する場合：

```bash
python run_mimamori.py --reset
python run_engineer.py --reset
python run_hinata.py --reset
python run_president.py --reset
```

## ファイル構成

### エンジン（全キャラ共通）

| ファイル | 役割 |
|---|---|
| `memory.py` | 記憶管理。日記の生記録（Storage）と内省の蓄積（Reflection）を JSON で保存・取得 |
| `state.py` | 感情状態の管理。自然言語で感情を保持し、毎日更新する作業記憶 |
| `llm_client.py` | Gemini API の呼び出し。マルチキー・ローテーションとフォールバック機構 |
| `external_context.py` | 外部コンテキストの読み込み。YAML から天気・ニュース等を読み取り、プロンプトに注入 |

### メイン実行スクリプト

| ファイル | 役割 |
|---|---|
| `phase6_with_reflection.py` | 蓮の日記生成。1日4ステップ（日記→感情更新→テーマ決定→振り返り）のメインループ |
| `run_mimamori.py` | ミマモリ3号（保育園の見守りAIカメラ）の日記生成 |
| `run_engineer.py` | 桐山拓真（AIエンジニア）の日記生成 |
| `run_hinata.py` | 星野ひなた（15歳の中学生）の日記生成 |
| `run_president.py` | ハリントン大統領（架空の米国大統領）の日記生成 |

### 人格設定

| ファイル | 役割 |
|---|---|
| `persona.py` | 佐伯蓮の人格設定（約1,500字の system prompt + 文体見本4つ） |
| `persona_mimamori.py` | ミマモリ3号の人格設定 |
| `persona_engineer.py` | 桐山拓真の人格設定 |
| `persona_hinata.py` | 星野ひなたの人格設定 |
| `persona_president.py` | ハリントン大統領の人格設定 |

### 外部コンテキスト（YAML）

| ファイル | 内容 |
|---|---|
| `context/tokyo_july_2026.yaml` | 蓮用。東京の天気・AIニュース・キャンパス情報 |
| `context/nursery_july_2026.yaml` | ミマモリ用。保育園の行事・天気 |
| `context/tech_april_2026.yaml` | 拓真用。テック業界ニュース・天気 |
| `context/school_april_2026.yaml` | ひなた用。学校生活・AI話題 |
| `context/whitehouse_april_2026.yaml` | ハリントン用。国際情勢・国内政治 |

### その他

| ファイル | 役割 |
|---|---|
| `evaluate.py` | 簡易的な参考指標の算出（bigram 重複率、TTR、切り口ユニーク率など） |
| `phase8_experience.py` | 7日間終了後に価値観の変化を要約する Experience 層の抽出 |
| `requirements.txt` | 依存ライブラリ |

## 設計の要点

- 出来事をハードコーディングしない。何が起きるかは AI が自律的に決定する
- 過去の出力を生文のまま翌日に渡さない。短いラベルや冒頭20字だけを渡し、自己模倣を防ぐ
- エンジンのコアモジュール（memory.py, state.py, llm_client.py, external_context.py）は全キャラ共通
