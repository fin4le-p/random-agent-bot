import os
import random
import time
import re
from collections import deque

import discord
from discord import app_commands
from dotenv import load_dotenv
from openai import OpenAI, APIError, APITimeoutError, BadRequestError, RateLimitError

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

groq_client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1",
)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

ai_group = app_commands.Group(
    name="ai",
    description="AI系コマンド群",
)

MODEL_CHOICES = [
    app_commands.Choice(name="【1】 早いが回答がおかしくなるかも（llama）", value=1),
    app_commands.Choice(name="【2】 速度も早くちょっとだけ優秀（gpt-oss）", value=2),
    app_commands.Choice(name="【3】 遅いが必ず動作し優秀（gpt）", value=3),
]

MODEL_MAP = {
    1: ("groq", "llama-3.1-8b-instant"),
    2: ("groq", "openai/gpt-oss-120b"),
    3: ("openai", "gpt-5-mini"),
}

TACTIC_RULES = """あなたは「VALORANT 戦術ジェネレーター」です。
ユーザーの状況に対して、1ラウンドで完結する具体的な作戦を1つ生成してください。

【前提】
・試合中に実行可能
・ラウンドを跨がない
・チームが即実行できる

【出力形式（厳守）】
1) タイトル: 12文字以内
2) 詳細: 1文。役割/場所/行動を必ず入れる
   - 役割: デュエリスト/イニシエーター/センチネル/コントローラー
   - 場所: Aサイト/Bサイト/ミッド/自陣/敵陣（ユーザーがCサイトやCサイトがあるマップを明記した場合のみCサイト可）
   - 行動: エントリー/ピーク/スモーク/フラッシュ/設置/リテイク/守り/ローテート/牽制/待機 から1〜2個
3) 注意: 1文

【共通ルール】
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

【前提】
・試合中に実行可能
・ラウンドを跨がない
・チームが即実行できる

【出力形式（厳守）】
1) タイトル: 12文字以内
2) 詳細: 1文。対象/場所/行動を必ず入れる
   - 対象: 投稿者 または 指定された人
   - 場所: Aサイト/Bサイト/ミッド/自陣/敵陣/指定なし（ユーザーがCサイトやCサイトがあるマップを明記した場合のみCサイト可）
   - 行動: 歩きのみ/しゃがみのみ/スキル使用禁止/スキル1回のみ/設置後はサイト内固定/リテイク時は最後尾/報告係に徹する/エコ時はゴースト固定/試合中は報告を2倍/設置役を必ず担当/リテイク時はスモーク役を担当/スキルは設置後のみ使用/撃ち合いは必ず1回引く/オペは拾わない/初動は情報取り専念
3) 注意: 1文

【共通ルール】
・エージェント名/マップ名/武器名/スキル名は「ユーザーが入力に含めた場合のみ」使用可。自分から新規に作らない
・ユーザー入力の固有名詞は、含まれていれば使ってよい。含まれていなければ使わない
・抽象表現や雰囲気ワードは禁止
・利敵行為、放置、暴言、回線切断は禁止
・勝利を著しく捨てる内容は禁止
・短く、断言調で書く
・戦術/作戦/ロール指定は入れない（罰ゲームに集中）
・情報が足りない場合は必ず上の選択肢から補完して埋める
"""

# ==============================
# 重複回避（直近履歴）
# ==============================
# モードごとに直近タイトルを保持（最大10件）
LAST_TITLES = {
    "tactic": deque(maxlen=10),
    "punish": deque(maxlen=10),
}

def _make_seed() -> str:
    return f"{int(time.time()*1000)}-{random.randint(1000, 9999)}"

def _extract_title(text: str) -> str | None:
    """
    例:
    1) タイトル: ○○○
    もしくは1行圧縮:
    1) タイトル: ○○○ 2) 詳細: ...
    """
    if not text:
        return None
    m = re.search(r"1\)\s*タイトル:\s*([^\n/]+)", text)
    if m:
        title = m.group(1).strip()
        # 次項目が同一行にあるなら切る
        title = re.split(r"\s+2\)\s*詳細:", title)[0].strip()
        return title[:40]
    return None

def _add_banlist(mode: str, prompt: str) -> str:
    banned = list(LAST_TITLES.get(mode, []))[-5:]
    if not banned:
        return prompt
    # タイトル縛りが一番効く
    return (
        prompt
        + "\n【禁止】次のタイトルと同一は出さない: "
        + " / ".join(banned)
    )

def _normalize_output(text: str) -> str:
    """
    改行でも1行でも読みやすく整形。
    1行圧縮のときは 2) 3) の前で改行を入れる。
    """
    t = (text or "").strip()
    # 1行圧縮を想定して 2) 3) の前で改行
    t = re.sub(r"\s+(?=[2-3]\)\s)", "\n", t)
    # 余計なスペース整形
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip()

def _select_client(model_value: int):
    provider, model = MODEL_MAP[model_value]
    if provider == "groq":
        if not GROQ_API_KEY:
            return None, None, "GROQ_API_KEY が .env にありません。"
        return groq_client, model, None
    if not OPENAI_API_KEY:
        return None, None, "OPENAI_API_KEY が .env にありません。"
    return openai_client, model, None

def _build_system_prompt(mode: str, hard: bool) -> str:
    if mode == "tactic":
        prompt = TACTIC_RULES
        if hard:
            prompt += "\nありえないくらい難しく調整してください。"
        return prompt

    prompt = PUNISH_RULES
    if hard:
        prompt += "\nありえないくらい難しく、利敵にならない範囲に調整してください。"
    return prompt

def _generate(mode: str, hard: bool, model_value: int, content: str | None) -> str:
    client, model, error = _select_client(model_value)
    if error:
        raise RuntimeError(error)

    # system prompt
    system_prompt = _build_system_prompt(mode, hard)
    system_prompt = _add_banlist(mode, system_prompt)

    # user prompt（毎回変える：seed混入 + 重複回避の明示）
    seed = _make_seed()
    base = (content or "おまかせで生成してください。").strip()
    user_content = (
        f"{base}\n"
        f"#seed:{seed}\n"
        f"直近と同じ案は避けてください。"
    )

    try:
        if model.startswith("gpt-"):
            response = client.responses.create(
                model=model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                reasoning={"effort": "low"},
                text={"verbosity": "low"},
                max_output_tokens=8000,
            )
            text = (response.output_text or "").strip()
        else:
            request_kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
            }
            request_kwargs["temperature"] = 0.7
            request_kwargs["max_tokens"] = 2000
            response = client.chat.completions.create(**request_kwargs)
            text = (response.choices[0].message.content or "").strip()
        #print(response.usage)
    except RateLimitError:
        raise RuntimeError("混雑中です。少し待ってから再実行してください。")
    except APITimeoutError:
        raise RuntimeError("タイムアウトしました。もう一度試してください。")
    except BadRequestError:
        raise RuntimeError("入力が長すぎるか不正です。短くして試してください。")
    except APIError:
        raise RuntimeError("APIエラーが発生しました。時間をおいて再試行してください。")

    text = _normalize_output(text)

    # 履歴更新
    title = _extract_title(text)
    if title:
        LAST_TITLES[mode].append(title)

    # 本文が空だったら（gpt-ossがやらかした場合の救済）
    if not text:
        return "（本文が空でした。別モデルを試して）"

    return text

# ==============================
# Commands
# ==============================
@ai_group.command(name="tactic", description="【開発中】AIによる戦術を考えてくれるモード")
@app_commands.describe(model="使用するモデル、回答が変わったりします", content="状況や要望（例：バインド攻めで、オペが出てきて連敗中）")
@app_commands.choices(model=MODEL_CHOICES)
async def tactic_cmd(
    interaction: discord.Interaction,
    model: app_commands.Choice[int],
    content: str,
):
    await interaction.response.defer()
    try:
        result = _generate("tactic", False, model.value, content)
    except Exception as exc:
        await interaction.followup.send(f"エラー: {exc}")
        return
    await interaction.followup.send(result)

@ai_group.command(name="tactic_hard", description="【開発中】AIによる戦術を考えてくれるモード（ハード）")
@app_commands.describe(model="使用するモデル、回答が変わったりします", content="状況や要望（例：バインド攻めで、オペが出てきて連敗中）")
@app_commands.choices(model=MODEL_CHOICES)
async def tactic_hard_cmd(
    interaction: discord.Interaction,
    model: app_commands.Choice[int],
    content: str,
):
    await interaction.response.defer()
    try:
        result = _generate("tactic", True, model.value, content)
    except Exception as exc:
        await interaction.followup.send(f"エラー: {exc}")
        return
    await interaction.followup.send(result)

@ai_group.command(name="punish", description="【開発中】AIによる罰ゲームを考えてくれるモード")
@app_commands.describe(model="使用するモデル、回答が変わったりします", content="mapや使ってるキャラを入力")
@app_commands.choices(model=MODEL_CHOICES)
async def punish_cmd(
    interaction: discord.Interaction,
    model: app_commands.Choice[int],
    content: str = "おまかせ",
):
    await interaction.response.defer()
    try:
        result = _generate("punish", False, model.value, content)
    except Exception as exc:
        await interaction.followup.send(f"エラー: {exc}")
        return
    await interaction.followup.send(result)

@ai_group.command(name="punish_hard", description="【開発中】AIによる罰ゲームを考えてくれるモード（ハード）")
@app_commands.describe(model="使用するモデル、回答が変わったりします", content="mapや使ってるキャラを入力")
@app_commands.choices(model=MODEL_CHOICES)
async def punish_hard_cmd(
    interaction: discord.Interaction,
    model: app_commands.Choice[int],
    content: str = "おまかせ",
):
    await interaction.response.defer()
    try:
        result = _generate("punish", True, model.value, content)
    except Exception as exc:
        await interaction.followup.send(f"エラー: {exc}")
        return
    await interaction.followup.send(result)
