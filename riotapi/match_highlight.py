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

# --- Discord embed safe limits (conservative) ---
EMBED_FIELD_VALUE_LIMIT = 1000          # Discord field value: max 1024
EMBED_DESCRIPTION_LIMIT = 3800          # Discord description: max 4096
EMBED_TOTAL_SOFT_LIMIT = 5800           # Discord total embed chars: max 6000
EMBED_MAX_FIELDS = 25

SUMMARY_COLOR = 0x5865F2
STORY_COLOR = 0x2B2D31

# --- Story prompt (stable delimiter output; headings are added by app, not by LLM) ---
STORY_PROMPT = """あなたはValorant配信者向けの「試合ストーリー脚本家」です。
入力は1試合分の統計とラウンドごとの個人成績です。
配信で盛り上がるように、試合の流れが頭に入る“物語＋実況”を日本語で作ってください。

【最重要ルール】
- 出力は必ず「6パート」を順番通りに出力すること。
- 各パートは必ず区切り行「---」で区切ること（区切り行はちょうど3文字のハイフン×3のみ）。
- 見出しラベル（例:「タイトル:」「1)」など）は一切書かない。中身だけを書く。
- ユーザーが理解できないメタ発言を禁止（例:「要確認」「summary」「JSON」「データ上」「根拠」「ログ」「推測」など）。
- 事実にない情報（武器名/エージェント名/サイト名/スキル/アルティ等）は書かない（曖昧表現で逃げるのも禁止）。
- 数字は入力JSONにあるものだけ。無い項目は書かない（0扱いで捏造もしない）。

【6パートの内容（順番固定）】
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


def _parse_discord_message(discord_message: str) -> tuple[str, list[str]]:
    lines = [line.strip() for line in (discord_message or "").splitlines() if line.strip()]
    if not lines:
        return "試合サマリー", ["詳細データなし"]
    return lines[0], lines[1:] or ["詳細データなし"]


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
    LLMが `---` 区切りで出した6パートを抽出する。
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
                        "次のJSONを元に、上のルール通りに6パートを `---` 区切りで出力してください。\n"
                        f"```json\n{payload_text}\n```"
                    ),
                },
            ],
            reasoning={"effort": "minimal"},
            text={"verbosity": "low"},
            max_output_tokens=2000,
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


async def _safe_delete_message(message: discord.Message | None):
    if message is None:
        return
    try:
        await message.delete()
    except Exception:
        pass


def _chunk_text(text: str, max_len: int) -> list[str]:
    """
    改行優先で安全にテキスト分割する。
    """
    text = (text or "").strip()
    if not text:
        return ["（空）"]

    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    current = ""

    for line in text.splitlines(keepends=True):
        if len(current) + len(line) <= max_len:
            current += line
            continue

        if current:
            chunks.append(current.rstrip())

        if len(line) <= max_len:
            current = line
        else:
            for i in range(0, len(line), max_len):
                part = line[i : i + max_len].rstrip()
                if part:
                    chunks.append(part)
            current = ""

    if current:
        chunks.append(current.rstrip())

    return chunks or ["（空）"]


def _embed_text_len(embed: discord.Embed) -> int:
    total = 0
    total += len(embed.title or "")
    total += len(embed.description or "")
    total += len(getattr(embed.footer, "text", "") or "")

    for field in embed.fields:
        total += len(field.name or "")
        total += len(field.value or "")

    return total


def _can_add_field(embed: discord.Embed, *, name: str, value: str) -> bool:
    if len(name) > 256:
        return False
    if len(value) > 1024:
        return False
    if len(embed.fields) >= EMBED_MAX_FIELDS:
        return False

    return (_embed_text_len(embed) + len(name) + len(value)) <= EMBED_TOTAL_SOFT_LIMIT


def _paginate_embeds(embeds: list[discord.Embed], footer_prefix: str) -> list[discord.Embed]:
    if not embeds:
        return embeds

    total = len(embeds)
    if total == 1:
        return embeds

    for i, embed in enumerate(embeds, start=1):
        embed.set_footer(text=f"{footer_prefix} {i}/{total}")
    return embeds


def _build_summary_embeds(*, riot_id: str, discord_message: str) -> list[discord.Embed]:
    summary_title, summary_lines = _parse_discord_message(discord_message)

    embeds: list[discord.Embed] = []
    current = discord.Embed(
        title="Match Highlight",
        description=f"```md\n{summary_title}\n```",
        color=SUMMARY_COLOR,
    )

    if riot_id:
        riot_field_value = f"`{riot_id}`"
        if _can_add_field(current, name="Riot ID", value=riot_field_value):
            current.add_field(name="Riot ID", value=riot_field_value, inline=False)
        else:
            embeds.append(current)
            current = discord.Embed(title="Match Highlight (続き)", color=SUMMARY_COLOR)
            current.add_field(name="Riot ID", value=riot_field_value, inline=False)

    summary_text = "\n".join([f"• {line}" for line in summary_lines])
    summary_chunks = _chunk_text(summary_text, EMBED_FIELD_VALUE_LIMIT)

    for i, chunk in enumerate(summary_chunks, start=1):
        field_name = "試合サマリー" if i == 1 else f"試合サマリー (続き{i})"
        if not _can_add_field(current, name=field_name, value=chunk):
            embeds.append(current)
            current = discord.Embed(title="Match Highlight (続き)", color=SUMMARY_COLOR)
        current.add_field(name=field_name, value=chunk, inline=False)

    if current.description or current.fields:
        embeds.append(current)

    return _paginate_embeds(embeds, "Summary")


def _build_story_fallback_embeds(story_text: str) -> list[discord.Embed]:
    """
    LLM出力が崩れたときのフォールバック。
    field ではなく description に分割して複数 embed にする。
    """
    raw_text = (story_text or "").strip() or "（生成結果が空でした）"
    raw_chunks = _chunk_text(raw_text, EMBED_DESCRIPTION_LIMIT)

    embeds: list[discord.Embed] = []
    for i, chunk in enumerate(raw_chunks, start=1):
        title = "ストーリー（フォールバック表示）" if i == 1 else "ストーリー（続き）"
        embed = discord.Embed(
            title=title,
            description=chunk,
            color=STORY_COLOR,
        )
        embeds.append(embed)

    return _paginate_embeds(embeds, "Story")


def _build_story_embeds(story_text: str) -> list[discord.Embed]:
    parts = _parse_story_sections(story_text)
    if len(parts) < 6:
        return _build_story_fallback_embeds(story_text)

    title_text = (parts[0] or "").strip() or "（タイトルなし）"
    sections = [
        ("30秒ダイジェスト", parts[1]),
        ("前半", parts[2]),
        ("後半", parts[3]),
        ("MVPハイライト", parts[4]),
        ("締め", parts[5]),
    ]

    embeds: list[discord.Embed] = []
    current = discord.Embed(
        title="ストーリー",
        description=f"**{title_text}**",
        color=STORY_COLOR,
    )

    for section_name, text in sections:
        section_chunks = _chunk_text((text or "").strip() or "（空）", EMBED_FIELD_VALUE_LIMIT)

        for idx, chunk in enumerate(section_chunks, start=1):
            field_name = section_name if idx == 1 else f"{section_name} (続き{idx})"

            if not _can_add_field(current, name=field_name, value=chunk):
                embeds.append(current)
                current = discord.Embed(
                    title="ストーリー（続き）",
                    color=STORY_COLOR,
                )

            current.add_field(name=field_name, value=chunk, inline=False)

    if current.description or current.fields:
        embeds.append(current)

    return _paginate_embeds(embeds, "Story")


async def run_match_highlight(
    *,
    interaction: discord.Interaction,
    post_json: PostJsonFunc,
    internal_api_key: str | None,
    final_ephemeral: bool,
):
    if not internal_api_key:
        return await interaction.followup.send("サーバー設定エラー: INTERNAL_API_KEY未設定", ephemeral=True)

    progress_message = await interaction.followup.send(
        "ハイライトを生成中です。しばらくお待ちください。",
        ephemeral=final_ephemeral,
        wait=True,
    )

    status_code, j, text = await post_json(
        "/internal/val/match-highlight",
        {"discord_user_id": int(interaction.user.id)},
        25,
    )

    if status_code != 200:
        output = _format_raw_response(status_code, j, text)
        logger.info("match-highlight error response:\n%s", output)
        wrapped = f"```json\n{output[:1700]}\n```"
        await _safe_delete_message(progress_message)
        return await interaction.followup.send(wrapped, ephemeral=True)

    if not (j or {}).get("ok", False):
        output = _format_business_error(j or {})
        logger.info("match-highlight business error response:\n%s", output)
        await _safe_delete_message(progress_message)
        for chunk in _split_message(output):
            await interaction.followup.send(chunk, ephemeral=final_ephemeral)
        return

    discord_message = (j or {}).get("discord_message", "")
    llm_payload = (j or {}).get("llm_payload")
    riot_id = (j or {}).get("riotId", "")

    # LLM生成はスレッドで
    raw_story = await asyncio.to_thread(_generate_story_from_payload, llm_payload)

    summary_embeds = _build_summary_embeds(
        riot_id=riot_id,
        discord_message=discord_message,
    )
    story_embeds = _build_story_embeds(raw_story)

    await _safe_delete_message(progress_message)

    for embed in summary_embeds:
        await interaction.followup.send(embed=embed, ephemeral=final_ephemeral)

    for embed in story_embeds:
        await interaction.followup.send(embed=embed, ephemeral=final_ephemeral)