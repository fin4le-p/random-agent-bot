import logging
import os
import random
import re
import time
from collections import deque

from dotenv import load_dotenv
from openai import APIError, APITimeoutError, BadRequestError, OpenAI, RateLimitError

load_dotenv()

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

MODEL_MAP: dict[int, tuple[str, str]] = {
    1: ("groq", "llama-3.1-8b-instant"),
    2: ("groq", "openai/gpt-oss-120b"),
    3: ("openai", "gpt-5-mini"),
}

MODEL_LABELS: dict[int, str] = {
    1: "ID1: 早いが不安定（llama）",
    2: "ID2: 速くてやや高品質（gpt-oss）",
    3: "ID3: 遅いが安定（gpt）",
}

TACTIC_RULES = """あなたは「VALORANT 戦術ジェネレーター」です。
ユーザーの状況に対して、1ラウンドで完結する具体的な作戦を1つ生成してください。

〖前提〗
・試合中に実行可能
・ラウンドを跨がない
・チームが即実行できる

〖出力形式（厳守）〗
1) タイトル: 12文字以内
2) 詳細: 1文。役割/場所/行動を必ず入れる
- 役割: デュエリスト/イニシエーター/センチネル/コントローラー
- 場所: Aサイト/Bサイト/ミッド/自陣/敵陣（ユーザーがCサイトやCサイトがあるマップを明記した場合のみCサイト可）
- 行動: エントリー/ピーク/スモーク/フラッシュ/設置/リテイク/守り/ローテート/牽制/待機 から1〜2個
3) 注意: 1文

〖共通ルール〗
・エージェント名/マップ名/武器名/スキル名は「ユーザーが入力に含めた場合のみ」使用可。自分から新規に作らない
・ユーザー入力の固有名詞は、含まれていれば使ってよい。含まれていなければ使わない
・抽象表現や雰囲気ワードは禁止
・利敵行為、放置、暴言、回線切断は禁止
・勝利を著しく捨てる内容は禁止
・短く、断言調で書く
・情報が足りない場合は必ず上の選択肢から補完して埋める
"""

PUNISH_RULES = """あなたは「VALORANT 罰ゲームジェネレーター」です。
試合中に投稿者（または指定された人）が実行する、1ラウンドで完結する罰ゲームを1つ生成してください。

〖前提〗
・試合中に実行可能
・ラウンドを跨がない
・チームが即実行できる

〖出力形式（厳守）〗
1) タイトル: 12文字以内
2) 詳細: 1文。対象/場所/行動を必ず入れる
- 対象: 投稿者 または 指定された人
- 場所: Aサイト/Bサイト/ミッド/自陣/敵陣/指定なし（ユーザーがCサイトやCサイトがあるマップを明記した場合のみCサイト可）
- 行動: 歩きのみ/しゃがみのみ/スキル使用禁止/スキル1回のみ/設置後はサイト内固定/リテイク時は最後尾/報告係に徹する/エコ時はゴースト固定/試合中は報告を2倍/設置役を必ず担当/リテイク時はスモーク役を担当/スキルは設置後のみ使用/撃ち合いは必ず1回引く/オペは拾わない/初動は情報取り専念
3) 注意: 1文

〖共通ルール〗
・エージェント名/マップ名/武器名/スキル名は「ユーザーが入力に含めた場合のみ」使用可。自分から新規に作らない
・ユーザー入力の固有名詞は、含まれていれば使ってよい。含まれていなければ使わない
・抽象表現や雰囲気ワードは禁止
・利敵行為、放置、暴言、回線切断は禁止
・勝利を著しく捨てる内容は禁止
・短く、断言調で書く
・戦術/作戦/ロール指定は入れない（罰ゲームに集中）
・情報が足りない場合は必ず上の選択肢から補完して埋める
"""

LAST_TITLES = {
    "tactic": deque(maxlen=10),
    "punish": deque(maxlen=10),
}

_groq_client: OpenAI | None = None
_openai_client: OpenAI | None = None


def _get_groq_client() -> OpenAI:
    global _groq_client
    if _groq_client is None:
        if not GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY が .env にありません。")
        _groq_client = OpenAI(
            api_key=GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
        )
    return _groq_client


def _get_openai_client() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY が .env にありません。")
        _openai_client = OpenAI(api_key=OPENAI_API_KEY)
    return _openai_client


def _make_seed() -> str:
    return f"{int(time.time() * 1000)}-{random.randint(1000, 9999)}"


def _extract_title(text: str) -> str | None:
    if not text:
        return None

    match = re.search(r"1\)\s*タイトル:\s*([^\n/]+)", text)
    if not match:
        return None

    title = match.group(1).strip()
    title = re.split(r"\s+2\)\s*詳細:", title)[0].strip()
    return title[:40] or None


def _add_banlist(mode: str, prompt: str) -> str:
    banned = list(LAST_TITLES.get(mode, []))[-5:]
    if not banned:
        return prompt
    return prompt + "\n〖禁止〗次のタイトルと同一は出さない: " + " / ".join(banned)


def _normalize_output(text: str) -> str:
    normalized = (text or "").strip()
    normalized = re.sub(r"\s+(?=[2-3]\)\s)", "\n", normalized)
    normalized = re.sub(r"[ \t]{2,}", " ", normalized)
    return normalized.strip()


def _select_client(model_value: int) -> tuple[OpenAI, str]:
    if model_value not in MODEL_MAP:
        raise RuntimeError(f"未対応のモデルIDです: {model_value}")

    provider, model = MODEL_MAP[model_value]

    if provider == "groq":
        return _get_groq_client(), model
    if provider == "openai":
        return _get_openai_client(), model

    raise RuntimeError(f"未対応のプロバイダです: {provider}")


def _build_system_prompt(mode: str, hard: bool) -> str:
    if mode == "tactic":
        prompt = TACTIC_RULES
        if hard:
            prompt += (
                "\n〖ハード専用ルール〗"
                "\n・通常より厳しくとても難しい内容にする"
                "\n・同時進行の動きを必ず入れる"
                "\n・フェイク/囮/逆サイドのいずれかを必ず含める"
                "\n・10秒以内など短い時間制限を入れる"
                "\n・失敗時のリスクを1文で明示する"
            )
        return prompt

    prompt = PUNISH_RULES
    if hard:
        prompt += (
            "\n〖ハード専用ルール〗"
            "\n・通常より厳しいとても難しい制約を1つ以上入れる"
            "\n・行動範囲/行動回数の制限を必ず含める"
            "\n・短い時間制限を入れる"
            "\n・失敗時のリスクを1文で明示する"
        )
    return prompt


def _build_user_content(content: str | None) -> str:
    seed = _make_seed()
    seed2 = _make_seed()
    focus_pool = ["情報取り", "フェイク", "逆サイド", "ラッシュ", "カウンター", "遅延"]
    tempo_pool = ["速攻", "中速", "遅め"]
    focus = random.choice(focus_pool)
    tempo = random.choice(tempo_pool)
    base = (content or "おまかせで生成してください。").strip()

    return (
        f"{base}\n"
        f"#seed:{seed}\n"
        f"#seed2:{seed2}\n"
        f"#focus:{focus}\n"
        f"#tempo:{tempo}\n"
        "直近と同じ案は避けてください。"
    )


def _call_model(client: OpenAI, model: str, system_prompt: str, user_content: str) -> str:
    if model.startswith("gpt-"):
        response = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            reasoning={"effort": "low"},
            text={"verbosity": "low"},
            max_output_tokens=3000,
        )
        return (response.output_text or "").strip()

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=0.7,
        max_tokens=2000,
    )
    content = response.choices[0].message.content
    return (content or "").strip()


def generate(mode: str, hard: bool, model_value: int, content: str | None) -> str:
    client, model = _select_client(model_value)
    system_prompt = _add_banlist(mode, _build_system_prompt(mode, hard))
    user_content = _build_user_content(content)

    try:
        text = _call_model(client, model, system_prompt, user_content)
    except RateLimitError as exc:
        raise RuntimeError("混雑中です。少し待ってから再実行してください。") from exc
    except APITimeoutError as exc:
        raise RuntimeError("タイムアウトしました。もう一度試してください。") from exc
    except BadRequestError as exc:
        raise RuntimeError("入力が長すぎるか不正です。短くして試してください。") from exc
    except APIError as exc:
        raise RuntimeError("APIエラーが発生しました。時間をおいて再試行してください。") from exc
    except Exception:
        logger.exception("Unexpected LLM error. mode=%s hard=%s model=%s", mode, hard, model_value)
        raise RuntimeError("モデル呼び出しに失敗しました。時間をおいて再試行してください。")

    text = _normalize_output(text)
    title = _extract_title(text)
    if title:
        LAST_TITLES[mode].append(title)

    if not text:
        return "（本文が空でした。別モデルを試してください）"

    return text