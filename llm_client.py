"""LLM プロバイダ抽象化レイヤー
Gemini 2.0/2.5, OpenAI, Claude のいずれを使うかを .env の LLM_PROVIDER で切り替えられる。
呼び出し側は `call_llm(...)` 1関数だけを使い、プロバイダ依存のパラメータ・型・例外を意識しない。

設計方針 (Anthropic Building Effective Agents 2024 / OpenAI Practical Guide 2025 準拠):
- システム側に「何が反復を抑制するか」を抽象化 (repetition_control 0.0〜1.0)
- 各プロバイダがそれぞれの方法で実現する
  - Gemini 2.0 / OpenAI: ネイティブ frequency_penalty
  - Gemini 2.5 / Claude: プロンプトに反復禁止指示を追記
- レスポンスは常に str を返す
- リトライは抽象化レイヤー側で一元的に処理
"""
from __future__ import annotations

import os
import re
import time
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

# どのプロバイダを使うかは .env で指定
DEFAULT_PROVIDER = os.getenv("LLM_PROVIDER", "gemini-2.5")

# プロバイダ別のデフォルトモデル名（必要なら .env で上書き可能）
DEFAULT_MODELS = {
    "gemini-2.0": "gemini-2.0-flash",
    "gemini-2.5": "gemini-2.5-flash",
    "openai":     "gpt-5-turbo",
    "claude":     "claude-opus-4-5",
}

# 反復抑制のためにプロンプト末尾に追記する文（API側で対応できないプロバイダ用）
ANTI_REPETITION_INSTRUCTION = (
    "\n【厳守】同じ表現や定型句、似た構文の反復を避けてください。"
    "これまで使った言い回しと違う言葉を選んでください。"
)


# ─────────────────────────────────────────────────────
# 公開関数
# ─────────────────────────────────────────────────────

class QuotaExhausted(Exception):
    """無料枠が尽きたことを表す例外。フォールバックの引き金になる。"""
    pass


class AllKeysExhausted(QuotaExhausted):
    """登録された全 API キーが尽きたことを表す例外。リトライしても無意味。"""
    pass


def _is_quota_error(err: Exception) -> bool:
    """例外メッセージを見て「枠切れ系のエラー」かどうか判定する。"""
    msg = str(err)
    return ("429" in msg or "RESOURCE_EXHAUSTED" in msg
            or "quota" in msg.lower() or "limit" in msg.lower())


def call_llm(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.7,
    max_output_tokens: Optional[int] = None,
    repetition_control: float = 0.0,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    fallback_model: Optional[str] = None,   # 同プロバイダ内のフォールバック先モデル名
    max_retries: int = 1,                   # 1 = 初回失敗後にもう1回だけ試す (合計2回)
) -> str:
    """LLM に問い合わせて文字列を返す共通関数。
    プロバイダごとの差異 (パラメータ名、型、レスポンス、例外) をすべて吸収する。

    fallback_model を指定すると、プライマリが枠切れエラーで失敗したとき
    自動で同プロバイダのフォールバックモデルに切り替えて再試行する。
    """
    provider = provider or DEFAULT_PROVIDER

    def _do(use_model: str) -> str:
        if provider == "gemini-2.0":
            return _retry(_call_gemini, max_retries,
                          provider="gemini-2.0", model=use_model,
                          system_prompt=system_prompt, user_prompt=user_prompt,
                          temperature=temperature, max_output_tokens=max_output_tokens,
                          repetition_control=repetition_control)
        if provider == "gemini-2.5":
            return _retry(_call_gemini, max_retries,
                          provider="gemini-2.5", model=use_model,
                          system_prompt=system_prompt, user_prompt=user_prompt,
                          temperature=temperature, max_output_tokens=max_output_tokens,
                          repetition_control=repetition_control)
        if provider == "openai":
            return _retry(_call_openai, max_retries,
                          model=use_model,
                          system_prompt=system_prompt, user_prompt=user_prompt,
                          temperature=temperature, max_output_tokens=max_output_tokens,
                          repetition_control=repetition_control)
        if provider == "claude":
            return _retry(_call_claude, max_retries,
                          model=use_model,
                          system_prompt=system_prompt, user_prompt=user_prompt,
                          temperature=temperature, max_output_tokens=max_output_tokens,
                          repetition_control=repetition_control)
        raise ValueError(f"Unknown provider: {provider}")

    primary = model or DEFAULT_MODELS.get(provider)
    try:
        return _do(primary)
    except Exception as primary_err:
        msg = str(primary_err)
        is_retriable = _is_quota_error(primary_err) or "503" in msg or "UNAVAILABLE" in msg
        if fallback_model and is_retriable:
            reason = "枠切れ" if _is_quota_error(primary_err) else "server error"
            print(f"    🔄 {primary} {reason} → fallback: {fallback_model}")
            try:
                return _do(fallback_model)
            except Exception as fb_err:
                # 両方ダメなら QuotaExhausted で明示的に通知
                if _is_quota_error(fb_err):
                    raise QuotaExhausted(
                        f"both {primary} and {fallback_model} exhausted. "
                        f"primary={primary_err}; fallback={fb_err}"
                    ) from fb_err
                raise
        raise


# ─────────────────────────────────────────────────────
# リトライ
# ─────────────────────────────────────────────────────

_RETRY_DELAY_PATTERN = re.compile(r"retry in (\d+(?:\.\d+)?)s", re.IGNORECASE)


def _extract_retry_delay(err: Exception, fallback: float = 30.0, cap: float = 90.0) -> float:
    """エラーメッセージから 'Please retry in 60s' 風の待ち時間を抽出する。
    見つからなければ fallback、見つかっても cap で頭打ち。
    """
    msg = str(err)
    m = _RETRY_DELAY_PATTERN.search(msg)
    if m:
        return min(float(m.group(1)), cap)
    return min(fallback, cap)


def _retry(fn, max_retries: int = 1, **kwargs) -> str:
    """賢いリトライ：
    - 指数バックオフは廃止（リトライそのものが quota を消費するため）
    - max_retries は実質「リトライ回数」(初回 + max_retries 回 = 合計 max_retries+1 回試行)
    - 429 / RESOURCE_EXHAUSTED の場合、エラーメッセージから retryDelay を読み取って
      その秒数だけ待機してから1回だけ試行する
    - 503 (サーバー側一時障害) は短い待機で1回試行
    - それ以外の例外は即座に raise
    """
    last_err: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            return fn(**kwargs)
        except AllKeysExhausted:
            # 全キー枯渇 → リトライ無意味、即raise
            raise
        except Exception as e:
            last_err = e
            msg = str(e)

            is_quota = ("429" in msg or "RESOURCE_EXHAUSTED" in msg
                        or "quota" in msg.lower())
            is_server = ("503" in msg or "UNAVAILABLE" in msg
                         or "500" in msg)

            if not (is_quota or is_server):
                # クォータでもサーバ障害でもない → リトライ不可
                raise

            if attempt == max_retries:
                # もうリトライ予算なし
                raise

            if is_quota:
                wait = _extract_retry_delay(e, fallback=60.0, cap=90.0)
                print(f"    ⏳ quota: {wait:.0f}秒待機してから再試行 ({attempt+1}/{max_retries+1})")
            else:
                wait = 5.0
                print(f"    ⏳ server error: {wait:.0f}秒待機してから再試行")

            time.sleep(wait)
    if last_err:
        raise last_err
    raise RuntimeError("retry loop fell through")


# ─────────────────────────────────────────────────────
# Gemini (2.0 と 2.5)
# ─────────────────────────────────────────────────────

_gemini_clients = None       # list[(label, client)]
_exhausted_keys: set[str] = set()  # 今セッションで枯渇したキーラベル


def _load_gemini_keys() -> list[tuple[str, str]]:
    """環境変数から Gemini API キーを全部拾う。
    GEMINI_API_KEY (必須) + GEMINI_API_KEY_2 〜 GEMINI_API_KEY_10 (オプション)
    返り値: [(ラベル, キー), ...] のリスト。
    """
    keys: list[tuple[str, str]] = []
    main_key = os.getenv("GEMINI_API_KEY")
    if main_key:
        keys.append(("KEY_1", main_key))
    for i in range(2, 11):
        k = os.getenv(f"GEMINI_API_KEY_{i}")
        if k:
            keys.append((f"KEY_{i}", k))
    return keys


def _get_gemini_clients():
    global _gemini_clients
    if _gemini_clients is None:
        from google import genai
        keys = _load_gemini_keys()
        if not keys:
            raise RuntimeError("GEMINI_API_KEY not set in .env")
        _gemini_clients = [(label, genai.Client(api_key=k)) for label, k in keys]
    return _gemini_clients


def _call_gemini(
    provider: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_output_tokens: Optional[int],
    repetition_control: float,
) -> str:
    """Gemini 2.0 / 2.5 共通の呼び出し。
    複数の API キーに対応し、枠切れ (429) のときは自動で次のキーに切り替える。
    全キーが尽きていれば AllKeysExhausted を投げる。
    """
    from google.genai import types

    # 2.5 は frequency_penalty 廃止 → プロンプト末尾に反復禁止指示を追記
    if provider == "gemini-2.5" and repetition_control > 0:
        user_prompt = user_prompt + ANTI_REPETITION_INSTRUCTION

    config_kwargs = dict(
        system_instruction=system_prompt,
        temperature=temperature,
    )
    # max_output_tokens は基本的に指定しない方が安全:
    # Gemini 2.5 系は内部に Thinking モードがあり、上限を狭く設定すると思考トークンに食われて
    # 出力テキストが極端に短くなる (debug_call.py で確認済み)。
    # 呼び出し側で本当に必要な場合のみ指定する。
    if max_output_tokens is not None:
        config_kwargs["max_output_tokens"] = max_output_tokens

    # 2.0 のみ frequency_penalty を使う (実際には 2.0 系は廃止されているが互換のため残す)
    if provider == "gemini-2.0" and repetition_control > 0:
        config_kwargs["frequency_penalty"] = round(repetition_control * 0.8, 2)
        config_kwargs["presence_penalty"] = round(repetition_control * 0.4, 2)

    clients = _get_gemini_clients()
    last_quota_err: Optional[Exception] = None
    tried = 0

    for label, client in clients:
        if label in _exhausted_keys:
            continue  # 既に今セッションで枯渇したキーはスキップ
        tried += 1
        try:
            response = client.models.generate_content(
                model=model,
                config=types.GenerateContentConfig(**config_kwargs),
                contents=user_prompt,
            )
            return response.text
        except Exception as e:
            err_msg = str(e)
            if _is_quota_error(e):
                # このキーは今日もう使えないので、セッション内でマークして次へ
                _exhausted_keys.add(label)
                last_quota_err = e
                print(f"    🔑 {label} 枠切れ → 次のキーを試行")
                continue
            if "503" in err_msg or "UNAVAILABLE" in err_msg:
                # サーバー混雑: このキーは一時的に使えないが、別キーなら通るかも
                print(f"    🔑 {label} server error → 次のキーを試行")
                last_quota_err = e
                continue
            # それ以外のエラーは即座に raise
            raise

    # 全キー尽きた
    if tried == 0:
        raise AllKeysExhausted("all Gemini keys are exhausted in this session")
    raise AllKeysExhausted(
        f"all {tried} Gemini keys exhausted today. last error: {last_quota_err}"
    )


# ─────────────────────────────────────────────────────
# OpenAI (frequency_penalty ネイティブサポート)
# ─────────────────────────────────────────────────────

_openai_client = None


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError("openai package not installed. pip install openai") from e
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not set in .env")
        _openai_client = OpenAI(api_key=api_key)
    return _openai_client


def _call_openai(
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_output_tokens: Optional[int],
    repetition_control: float,
) -> str:
    client = _get_openai_client()
    kwargs = dict(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=temperature,
    )
    if max_output_tokens is not None:
        kwargs["max_tokens"] = max_output_tokens
    if repetition_control > 0:
        # OpenAI は -2.0〜2.0。0.0〜1.0 → 0.0〜1.0 でそのまま。
        kwargs["frequency_penalty"] = round(repetition_control, 2)
        kwargs["presence_penalty"]  = round(repetition_control * 0.5, 2)

    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content


# ─────────────────────────────────────────────────────
# Claude (frequency_penalty なし → プロンプトで対応)
# ─────────────────────────────────────────────────────

_claude_client = None


def _get_claude_client():
    global _claude_client
    if _claude_client is None:
        try:
            import anthropic
        except ImportError as e:
            raise RuntimeError("anthropic package not installed") from e
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set in .env")
        _claude_client = anthropic.Anthropic(api_key=api_key)
    return _claude_client


def _call_claude(
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_output_tokens: Optional[int],
    repetition_control: float,
) -> str:
    if repetition_control > 0:
        user_prompt = user_prompt + ANTI_REPETITION_INSTRUCTION

    client = _get_claude_client()
    response = client.messages.create(
        model=model,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        temperature=temperature,
        max_tokens=max_output_tokens or 1024,
    )
    return response.content[0].text
