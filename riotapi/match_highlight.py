# riotapi.py
import asyncio
import json
import logging
import os
from typing import Awaitable, Callable

import discord
from openai import OpenAI, APIError, APITimeoutError, BadRequestError, RateLimitError

PostJsonFunc = Callable[[str, dict, int], Awaitable[tuple[int, dict, str]]]
logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MATCH_HIGHLIGHT_MODEL = os.getenv("MATCH_HIGHLIGHT_MODEL", "gpt-5-mini")
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# --- Story prompt (stable delimiter output; headings are added by app, not by LLM) ---
STORY_PROMPT = """あなたはValorant配信者向けの「試合ストーリー脚本家」です。
入力は1試合分の統計とラウンドごとの個人成績です。
配信で盛り上がるように、試合の流れが頭に入る“物語＋実況”を日本語で作ってください。

【最重要ルール】
- 出力は必ず「7パート」を順番通りに出力すること。
- 各パートは必ず区切り行「---」で区切ること（区切り行はちょうど3文字のハイフン×3のみ）。
- 見出しラベル（例:「タイトル:」「1)」など）は一切書かない。中身だけを書く。
- ユーザーが理解できないメタ発言を禁止（例:「要確認」「summary」「JSON」「データ上」「根拠」「ログ」「推測」など）。
- 事実にない情報（武器名/エージェント名/サイト名/スキル/アルティ等）は書かない（曖昧表現で逃げるのも禁止）。
- 数字は入力JSONにあるものだけ。無い項目は書かない（0扱いで捏造もしない）。

【7パートの内容（順番固定）】
パート1: タイトル（1行・煽り系）
---
パート2: 30秒ダイジェスト（3〜5文）
---
パート3: 前半の流れ（重要ラウンド最大3つ。各1〜2文で実況）
---
パート4: 後半の流れ（重要ラウンド最大3つ。各1〜2文で実況）
---
パート5: MVPハイライト（ベスト3。各2〜3文）
---
パート6: 締めコメント案（強気/おふざけ の2本。各1〜2文）
---
パート7: 数字で見る今日（K/D, ACS, HS率, FB-FD, マルチキル, 最大ダメージR を箇条書き）
  - FB-FDは入力に数があるときのみ「FB-FD: x-y」で出す。曖昧な注釈は禁止。

【重要ラウンド抽出ルール】
次のいずれかに当てはまるラウンドを優先：
- multiKill >= 2
- firstBlood == true
- clutchAttemptVs > 0（勝敗問わず）
- damage が試合内で上位（特に最大ダメージ）
- 連取の起点っぽいラウンド（wonが続く区間の最初）
- 12点目/13点目など節目

【入力データ】
以下のJSONだけを根拠に書いてください："""


def _format_raw_response(status_code: int, json_body: dict, raw_text: str) -> str:
    if json_body and "raw" not in json_body:
        body_text = json.dumps(json_body, ensure_ascii=False)
    else:
        body_text = raw_text or json.dumps(json_body, ensure_ascii=False)
    return f"status={status_code}\n{body_text}"


def _format_discord_message(discord_message: str) -> str:
    """
    API側の discord_message をDiscord表示用に整形
    - 1行目を太字タイトル
    - 以降は箇条書き
    """
    lines = [line.strip() for line in (discord_message or "").splitlines() if line.strip()]
    if not lines:
        return "（discord_message が空です）"

    title = lines[0]
    body = [f"- {line}" for line in lines[1:]]
    if not body:
        body = ["- 詳細データなし"]
    return f"**{title}**\n" + "\n".join(body)


def _format_meta_info(riot_id: str, region: str, game_start_at_jst: str | None) -> str:
    lines = []
    if riot_id:
        lines.append(f"RiotID: `{riot_id}`")
    if region:
        lines.append(f"Region: `{region}`")
    if game_start_at_jst:
        lines.append(f"試合開始(JST): `{game_start_at_jst}`")
    return "\n".join(lines)


def _format_business_error(j: dict) -> str:
    riot_id = (j or {}).get("riotId") or "Unknown"
    region = (j or {}).get("region") or "?"
    error_code = (j or {}).get("error") or "unknown_error"
    message = (j or {}).get("message") or "match-highlight の取得に失敗しました。"

    return (
        "## Match Highlight\n"
        f"RiotID: `{riot_id}`\n"
        f"Region: `{region}`\n"
        f"Error: `{error_code}`\n"
        f"{message}"
    )


def _extract_response_text(response) -> str:
    """
    OpenAI Responses API の戻りからテキストを抽出
    """
    text = (getattr(response, "output_text", "") or "").strip()
    if text:
        return text

    chunks = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            if getattr(content, "type", "") == "output_text" and getattr(content, "text", None):
                chunks.append(content.text)
    return "\n".join(chunks).strip()


def _parse_story_sections(story_text: str) -> list[str]:
    """
    LLMが `---` 区切りで出した7パートを抽出する。
    崩れたときでも落ちないようにフォールバックする。
    """
    t = (story_text or "").strip()
    if not t:
        return []

    # まずは「改行 --- 改行」を優先
    parts = [p.strip() for p in t.split("\n---\n")]
    if len(parts) == 1:
        # 崩れて「---」だけになった場合の保険
        parts = [p.strip() for p in t.split("---")]

    parts = [p for p in parts if p]
    return parts


def _format_story_for_discord(story_text: str) -> str:
    """
    7パートをDiscord向けに見出し付きで整形する。
    LLMには見出しを書かせないので、ここで確実に付ける。
    """
    parts = _parse_story_sections(story_text)

    if len(parts) < 7:
        # フォーマットが崩れた場合は、そのまま出す（最低限）
        return story_text.strip() or "（生成結果が空でした）"

    title, digest, first_half, second_half, mvp, ending, numbers = parts[:7]

    return (
        "【タイトル】\n"
        f"{title}\n\n"
        "【30秒ダイジェスト】\n"
        f"{digest}\n\n"
        "【前半】\n"
        f"{first_half}\n\n"
        "【後半】\n"
        f"{second_half}\n\n"
        "【MVPハイライト】\n"
        f"{mvp}\n\n"
        "【締め】\n"
        f"{ending}\n\n"
        "【数字で見る今日】\n"
        f"{numbers}"
    )


def _generate_story_from_payload(llm_payload) -> str:
    if not llm_payload:
        return "（llm_payload がないため生成できませんでした）"
    if not openai_client:
        return "（OPENAI_API_KEY 未設定のため生成できませんでした）"

    payload_text = llm_payload if isinstance(llm_payload, str) else json.dumps(llm_payload, ensure_ascii=False, indent=2)

    try:
        response = openai_client.responses.create(
            model=MATCH_HIGHLIGHT_MODEL,
            input=[
                {"role": "system", "content": STORY_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "次のJSONを元に、上のルール通りに7パートを `---` 区切りで出力してください。\n"
                        f"```json\n{payload_text}\n```"
                    ),
                },
            ],
            # ここは軽めでOK（構造化と実況テンションが主）
            reasoning={"effort": "minimal"},
            text={"verbosity": "low"},
            # MAX OUTPUTは上げてOKとのことなので増やす（Discord側はsplitする）
            max_output_tokens=3000,
        )
        story = _extract_response_text(response)
        return story or "（生成結果が空でした）"
    except RateLimitError:
        return "（生成APIが混雑中です。少し待って再実行してください）"
    except APITimeoutError:
        return "（生成APIがタイムアウトしました。再実行してください）"
    except BadRequestError:
        return "（llm_payloadの入力が不正または長すぎるため生成できませんでした）"
    except APIError:
        return "（生成APIでエラーが発生しました。時間を置いて再実行してください）"


def _split_message(text: str, chunk_size: int = 1800) -> list[str]:
    """
    Discord送信向けに分割（見出しやコードブロック崩れにくいよう改行単位で分割）
    """
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) <= chunk_size:
            current += line
            continue
        if current:
            chunks.append(current)
        if len(line) <= chunk_size:
            current = line
        else:
            for i in range(0, len(line), chunk_size):
                chunks.append(line[i : i + chunk_size])
            current = ""
    if current:
        chunks.append(current)
    return chunks


async def run_match_highlight(
    *,
    interaction: discord.Interaction,
    post_json: PostJsonFunc,
    internal_api_key: str | None,
    final_ephemeral: bool,
):
    if not internal_api_key:
        return await interaction.followup.send("サーバー設定エラー: INTERNAL_API_KEY未設定", ephemeral=True)

    status_code, j, text = await post_json(
        "/internal/val/match-highlight",
        {"discord_user_id": int(interaction.user.id)},
        25,
    )

    if status_code != 200:
        output = _format_raw_response(status_code, j, text)
        logger.info("match-highlight error response:\n%s", output)
        wrapped = f"```json\n{output[:1700]}\n```"
        return await interaction.followup.send(wrapped, ephemeral=True)

    if not (j or {}).get("ok", False):
        output = _format_business_error(j or {})
        logger.info("match-highlight business error response:\n%s", output)
        for chunk in _split_message(output):
            await interaction.followup.send(chunk, ephemeral=final_ephemeral)
        return

    discord_message = (j or {}).get("discord_message", "")
    llm_payload = (j or {}).get("llm_payload")
    riot_id = (j or {}).get("riotId", "")
    region = (j or {}).get("region", "")
    game_start_at_jst = (j or {}).get("gameStartAtJST")

    summary_block = _format_discord_message(discord_message)
    meta_block = _format_meta_info(riot_id, region, game_start_at_jst)

    # LLM生成はスレッドで
    raw_story = await asyncio.to_thread(_generate_story_from_payload, llm_payload)

    # 見出し付け＆7パート整形はアプリ側で確実に
    story_block = _format_story_for_discord(raw_story)

    output = (
        "## 1) Match Highlight\n"
        f"{meta_block}\n"
        f"{summary_block}\n\n"
        "## 2) AIストーリー\n"
        f"{story_block}"
    )

    logger.info("match-highlight formatted response:\n%s", output)

    for chunk in _split_message(output):
        await interaction.followup.send(chunk, ephemeral=final_ephemeral)
