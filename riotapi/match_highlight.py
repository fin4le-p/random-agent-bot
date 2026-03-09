# riotapi.py
import asyncio
import json
import logging
import os
import re
from itertools import islice
from typing import Awaitable, Callable

import discord
from openai import OpenAI, APIError, APITimeoutError, BadRequestError, RateLimitError

PostJsonFunc = Callable[[str, dict, int], Awaitable[tuple[int, dict, str]]]
logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MATCH_HIGHLIGHT_MODEL = os.getenv("MATCH_HIGHLIGHT_MODEL", "gpt-5-mini")
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# --- Discord embed safe limits ---
EMBED_FIELD_VALUE_LIMIT = 1000          # Discord field value max=1024
EMBED_DESCRIPTION_LIMIT = 3800          # Discord description max=4096
EMBED_TOTAL_SOFT_LIMIT = 5800           # Discord embed total max=6000
EMBED_MAX_FIELDS = 25
EMBEDS_PER_MESSAGE = 10                # Discord message embeds max=10

SUMMARY_COLOR = 0x5865F2
STORY_COLOR = 0x2B2D31
PROGRESS_COLOR = 0xF1C40F
ERROR_COLOR = 0xED4245

# --- Story prompt ---
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

【読みやすさルール】
- 1文はできるだけ短くする。
- パート2は1文ごとに改行する。
- パート3とパート4は、重要ラウンドごとに改行する。
- パート5は、ベスト3を1項目ずつ改行する。
- パート6は、2案をそれぞれ改行して分ける。
- 箇条書き記号（・、-、1. など）は使わない。改行だけで見やすくする。

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

    parts = [p.strip() for p in t.split("\n---\n")]
    if len(parts) == 1:
        parts = [p.strip() for p in t.split("---")]

    return [p for p in parts if p]


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


def _truncate(text: str, max_len: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _strip_prefix(line: str) -> str:
    line = (line or "").strip()
    line = re.sub(r"^[\-\*•■◆▶▷☑️✅\d\.\)\(]+\s*", "", line)
    return line.strip()


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


def _split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    parts = re.split(r"(?<=[。！？!?])\s*", text)
    return [p.strip() for p in parts if p.strip()]


def _extract_blocks(text: str, *, max_items: int = 6, sentences_per_block: int = 2) -> list[str]:
    """
    可読性のためにテキストを小さめの意味ブロックに分ける。
    - 改行があれば改行を優先
    - 改行がなければ文単位で分ける
    """
    lines = [_strip_prefix(line) for line in (text or "").splitlines() if line.strip()]
    lines = [line for line in lines if line]

    if len(lines) >= 2:
        return lines[:max_items]

    sentences = _split_sentences(text)
    if not sentences:
        t = (text or "").strip()
        return [t] if t else ["（空）"]

    if len(sentences) == 1:
        return [sentences[0]]

    blocks: list[str] = []
    current: list[str] = []
    for sentence in sentences:
        current.append(sentence)
        if len(current) >= sentences_per_block:
            blocks.append(" ".join(current).strip())
            current = []
    if current:
        blocks.append(" ".join(current).strip())

    return blocks[:max_items] or ["（空）"]


def _extract_close_comments(text: str) -> list[str]:
    lines = [_strip_prefix(line) for line in (text or "").splitlines() if line.strip()]
    lines = [line for line in lines if line]
    if len(lines) >= 2:
        return [lines[0], lines[1]]

    sentences = _split_sentences(text)
    if len(sentences) >= 2:
        mid = max(1, len(sentences) // 2)
        left = " ".join(sentences[:mid]).strip()
        right = " ".join(sentences[mid:]).strip()
        if left and right:
            return [left, right]

    raw = (text or "").strip()
    return [raw or "（空）"]


def _build_bullet_blocks(lines: list[str], *, max_lines_per_block: int = 3, max_chars_per_block: int = 750) -> list[str]:
    cleaned = [_strip_prefix(line) for line in lines if (line or "").strip()]
    cleaned = [line for line in cleaned if line]
    if not cleaned:
        return ["• 詳細データなし"]

    blocks: list[str] = []
    current: list[str] = []

    for line in cleaned:
        bullet = f"• {line}"
        candidate_lines = current + [bullet]
        candidate_text = "\n".join(candidate_lines)
        if current and (len(candidate_lines) > max_lines_per_block or len(candidate_text) > max_chars_per_block):
            blocks.append("\n".join(current))
            current = [bullet]
        else:
            current = candidate_lines

    if current:
        blocks.append("\n".join(current))

    return blocks or ["• 詳細データなし"]


def _embed_text_len(embed: discord.Embed) -> int:
    total = 0
    total += len(embed.title or "")
    total += len(embed.description or "")
    total += len(getattr(embed.footer, "text", "") or "")
    total += len(getattr(embed.author, "name", "") or "")

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


def _new_embed(title: str, *, description: str | None = None, color: int = STORY_COLOR) -> discord.Embed:
    return discord.Embed(
        title=_truncate(title, 256),
        description=_truncate(description, EMBED_DESCRIPTION_LIMIT) if description else None,
        color=color,
    )


def _build_progress_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎮 Match Highlight",
        description=(
            "**ハイライトを生成中です**\n"
            "> 試合データを解析して、配信用の見やすい形に整えています。"
        ),
        color=PROGRESS_COLOR,
    )
    embed.add_field(
        name="進行",
        value=(
            "`1/3` 試合データ取得\n"
            "`2/3` ストーリー生成\n"
            "`3/3` Discord表示用に整形"
        ),
        inline=False,
    )
    embed.set_footer(text="数秒かかることがあります")
    return embed


def _build_public_error_notice_embed() -> discord.Embed:
    return discord.Embed(
        title="⚠️ Match Highlight",
        description=(
            "ハイライト生成中にエラーが発生しました。\n"
            "詳細は実行者にのみ表示しています。"
        ),
        color=ERROR_COLOR,
    )


def _build_status_error_embeds(status_code: int, json_body: dict, raw_text: str) -> list[discord.Embed]:
    output = _format_raw_response(status_code, json_body, raw_text)
    embed = discord.Embed(
        title="❌ Match Highlight Error",
        description="内部APIの応答でエラーが返されました。",
        color=ERROR_COLOR,
    )
    embed.add_field(name="Status", value=f"`{status_code}`", inline=True)

    chunks = _chunk_text(output, EMBED_FIELD_VALUE_LIMIT)
    embeds: list[discord.Embed] = [embed]
    current = embed

    for i, chunk in enumerate(chunks, start=1):
        field_name = "レスポンス" if i == 1 else f"レスポンス (続き{i})"
        value = f"```json\n{chunk}\n```"
        if len(value) > EMBED_FIELD_VALUE_LIMIT:
            value = chunk
        if not _can_add_field(current, name=field_name, value=value):
            current = _new_embed("❌ Match Highlight Error (続き)", color=ERROR_COLOR)
            embeds.append(current)
        current.add_field(name=field_name, value=value, inline=False)

    return _paginate_embeds(embeds, "Error")


def _build_business_error_embeds(j: dict) -> list[discord.Embed]:
    riot_id = (j or {}).get("riotId") or "Unknown"
    region = (j or {}).get("region") or "?"
    error_code = (j or {}).get("error") or "unknown_error"
    message = (j or {}).get("message") or "match-highlight の取得に失敗しました。"

    embed = discord.Embed(
        title="⚠️ Match Highlight",
        description="ハイライトの生成条件を満たせなかったため、処理を終了しました。",
        color=ERROR_COLOR,
    )
    embed.add_field(name="Riot ID", value=f"`{riot_id}`", inline=True)
    embed.add_field(name="Region", value=f"`{region}`", inline=True)
    embed.add_field(name="Error", value=f"`{error_code}`", inline=False)

    for chunk in _chunk_text(message, EMBED_FIELD_VALUE_LIMIT):
        embed.add_field(name="詳細", value=chunk, inline=False)

    return [embed]


def _build_field_card_embeds(
    *,
    title: str,
    description: str | None,
    entries: list[str],
    label_fn,
    color: int,
) -> list[discord.Embed]:
    embeds: list[discord.Embed] = []
    current = _new_embed(title, description=description, color=color)
    continued_title = f"{title}（続き）"

    for entry_idx, entry in enumerate(entries, start=1):
        safe_entry = (entry or "").strip() or "（空）"
        chunks = _chunk_text(safe_entry, EMBED_FIELD_VALUE_LIMIT)

        for chunk_idx, chunk in enumerate(chunks, start=1):
            field_name = label_fn(entry_idx, chunk_idx)
            if not _can_add_field(current, name=field_name, value=chunk):
                embeds.append(current)
                current = _new_embed(continued_title, color=color)
            current.add_field(name=field_name, value=chunk, inline=False)

    if current.description or current.fields:
        embeds.append(current)

    return embeds


def _build_summary_embeds(*, riot_id: str, discord_message: str) -> list[discord.Embed]:
    summary_title, summary_lines = _parse_discord_message(discord_message)
    summary_blocks = _build_bullet_blocks(summary_lines, max_lines_per_block=3, max_chars_per_block=700)

    embeds: list[discord.Embed] = []
    current = discord.Embed(
        title="🎯 Match Highlight",
        description=f"> {_truncate(summary_title, 300)}",
        color=SUMMARY_COLOR,
    )

    if riot_id:
        riot_field_value = f"`{riot_id}`"
        if _can_add_field(current, name="Riot ID", value=riot_field_value):
            current.add_field(name="Riot ID", value=riot_field_value, inline=False)
        else:
            embeds.append(current)
            current = _new_embed("🎯 Match Highlight（続き）", color=SUMMARY_COLOR)
            current.add_field(name="Riot ID", value=riot_field_value, inline=False)

    for i, block in enumerate(summary_blocks, start=1):
        block_chunks = _chunk_text(block, EMBED_FIELD_VALUE_LIMIT)

        for j, chunk in enumerate(block_chunks, start=1):
            if i == 1 and j == 1:
                field_name = "試合メモ"
            elif j == 1:
                field_name = f"試合メモ {i}"
            else:
                field_name = f"試合メモ {i} (続き{j})"

            if not _can_add_field(current, name=field_name, value=chunk):
                embeds.append(current)
                current = _new_embed("🎯 Match Highlight（続き）", color=SUMMARY_COLOR)

            current.add_field(name=field_name, value=chunk, inline=False)

    if current.description or current.fields:
        embeds.append(current)

    return _paginate_embeds(embeds, "Summary")


def _build_story_intro_embeds(title_text: str, digest_text: str) -> list[discord.Embed]:
    digest_items = _extract_blocks(digest_text, max_items=6, sentences_per_block=1)
    digest_blocks = _build_bullet_blocks(digest_items, max_lines_per_block=4, max_chars_per_block=750)

    embeds = _build_field_card_embeds(
        title="🔥 Story Intro",
        description=f"**{_truncate(title_text, 280)}**",
        entries=digest_blocks,
        label_fn=lambda i, j: "30秒ダイジェスト" if i == 1 and j == 1 else f"30秒ダイジェスト {i}" if j == 1 else f"30秒ダイジェスト {i} (続き{j})",
        color=STORY_COLOR,
    )
    return embeds


def _build_flow_embeds(section_title: str, text: str, *, emoji: str) -> list[discord.Embed]:
    blocks = _extract_blocks(text, max_items=6, sentences_per_block=2)

    embeds = _build_field_card_embeds(
        title=f"{emoji} {section_title}",
        description="3つの見所を紹介！",
        entries=blocks,
        label_fn=lambda i, j: f"見どころ {i}" if j == 1 else f"見どころ {i} (続き{j})",
        color=STORY_COLOR,
    )
    return embeds


def _build_mvp_embeds(text: str) -> list[discord.Embed]:
    blocks = _extract_blocks(text, max_items=3, sentences_per_block=2)
    labels = ["TOP 1", "TOP 2", "TOP 3"]

    embeds = _build_field_card_embeds(
        title="⭐ MVP ハイライト",
        description="刺さったプレーを上から順にピックアップ！",
        entries=blocks,
        label_fn=lambda i, j: labels[i - 1] if i - 1 < len(labels) and j == 1 else f"{labels[i - 1]} (続き{j})" if i - 1 < len(labels) else f"MVP {i}",
        color=STORY_COLOR,
    )
    return embeds


def _build_close_embeds(text: str) -> list[discord.Embed]:
    comments = _extract_close_comments(text)

    if len(comments) >= 2:
        embeds = _build_field_card_embeds(
            title="🎤 みんなに向けてコメント",
            description="みんな聞いてくれ！",
            entries=[comments[0], comments[1]],
            label_fn=lambda i, j: ("強気" if i == 1 else "おふざけ") if j == 1 else (f"{'強気' if i == 1 else 'おふざけ'} (続き{j})"),
            color=STORY_COLOR,
        )
        return embeds

    return _build_field_card_embeds(
        title="🎤 配信用コメント",
        description="締めコメント。",
        entries=comments,
        label_fn=lambda i, j: "締め" if i == 1 and j == 1 else f"締め (続き{j})",
        color=STORY_COLOR,
    )


def _build_story_fallback_embeds(story_text: str) -> list[discord.Embed]:
    """
    LLM出力が崩れたときのフォールバック。
    長文をそのまま貼らず、断片カードとして表示する。
    """
    raw_text = (story_text or "").strip() or "（生成結果が空でした）"
    blocks = _extract_blocks(raw_text, max_items=8, sentences_per_block=2)

    if len(blocks) <= 1 and len(raw_text) > EMBED_FIELD_VALUE_LIMIT:
        raw_chunks = _chunk_text(raw_text, EMBED_DESCRIPTION_LIMIT)
        embeds: list[discord.Embed] = []
        for i, chunk in enumerate(raw_chunks, start=1):
            title = "📖 ストーリー（フォールバック）" if i == 1 else "📖 ストーリー（続き）"
            embed = discord.Embed(
                title=title,
                description=chunk,
                color=STORY_COLOR,
            )
            embeds.append(embed)
        return _paginate_embeds(embeds, "Fallback")

    embeds = _build_field_card_embeds(
        title="📖 ストーリー（フォールバック）",
        description="区切り出力が崩れたため、安全な表示モードで出しています。",
        entries=blocks,
        label_fn=lambda i, j: f"断片 {i}" if j == 1 else f"断片 {i} (続き{j})",
        color=STORY_COLOR,
    )
    return _paginate_embeds(embeds, "Fallback")


def _build_story_embeds(story_text: str) -> list[discord.Embed]:
    parts = _parse_story_sections(story_text)
    if len(parts) < 6:
        return _build_story_fallback_embeds(story_text)

    title_text = (parts[0] or "").strip() or "（タイトルなし）"
    digest_text = (parts[1] or "").strip() or "（空）"
    first_half_text = (parts[2] or "").strip() or "（空）"
    second_half_text = (parts[3] or "").strip() or "（空）"
    mvp_text = (parts[4] or "").strip() or "（空）"
    close_text = (parts[5] or "").strip() or "（空）"

    embeds: list[discord.Embed] = []
    embeds.extend(_build_summary_embeds(riot_id="", discord_message=""))  # placeholder avoided below
    embeds.clear()

    embeds.extend(_build_story_intro_embeds(title_text, digest_text))
    embeds.extend(_build_flow_embeds("前半の流れ", first_half_text, emoji="🟦"))
    embeds.extend(_build_flow_embeds("後半の流れ", second_half_text, emoji="🟥"))
    embeds.extend(_build_mvp_embeds(mvp_text))
    embeds.extend(_build_close_embeds(close_text))

    return _paginate_embeds(embeds, "Story")


def _batched(seq: list[discord.Embed], size: int = EMBEDS_PER_MESSAGE):
    it = iter(seq)
    while True:
        batch = list(islice(it, size))
        if not batch:
            break
        yield batch


async def _send_embed_batches(
    *,
    interaction: discord.Interaction,
    embeds: list[discord.Embed],
    ephemeral: bool,
):
    for batch in _batched(embeds, EMBEDS_PER_MESSAGE):
        await interaction.followup.send(embeds=batch, ephemeral=ephemeral)


async def _edit_message_and_send_rest(
    *,
    message: discord.WebhookMessage,
    interaction: discord.Interaction,
    embeds: list[discord.Embed],
    ephemeral: bool,
):
    if not embeds:
        embeds = [
            discord.Embed(
                title="Match Highlight",
                description="表示できるデータがありませんでした。",
                color=STORY_COLOR,
            )
        ]

    first_batch = embeds[:EMBEDS_PER_MESSAGE]
    rest = embeds[EMBEDS_PER_MESSAGE:]

    await message.edit(embeds=first_batch)

    if rest:
        await _send_embed_batches(
            interaction=interaction,
            embeds=rest,
            ephemeral=ephemeral,
        )


async def run_match_highlight(
    *,
    interaction: discord.Interaction,
    post_json: PostJsonFunc,
    internal_api_key: str | None,
    final_ephemeral: bool,
):
    if not internal_api_key:
        embed = discord.Embed(
            title="⚠️ Match Highlight",
            description="サーバー設定エラー: `INTERNAL_API_KEY` が未設定です。",
            color=ERROR_COLOR,
        )
        return await interaction.followup.send(embed=embed, ephemeral=True)

    progress_message = await interaction.followup.send(
        embed=_build_progress_embed(),
        ephemeral=final_ephemeral,
        wait=True,
    )

    status_code, j, text = await post_json(
        "/internal/val/match-highlight",
        {"discord_user_id": int(interaction.user.id)},
        25,
    )

    if status_code != 200:
        logger.info("match-highlight error response:\n%s", _format_raw_response(status_code, j, text))
        error_embeds = _build_status_error_embeds(status_code, j, text)

        if final_ephemeral:
            return await _edit_message_and_send_rest(
                message=progress_message,
                interaction=interaction,
                embeds=error_embeds,
                ephemeral=True,
            )

        await progress_message.edit(embed=_build_public_error_notice_embed())
        return await _send_embed_batches(
            interaction=interaction,
            embeds=error_embeds,
            ephemeral=True,
        )

    if not (j or {}).get("ok", False):
        logger.info("match-highlight business error response:\n%s", json.dumps(j or {}, ensure_ascii=False))
        business_error_embeds = _build_business_error_embeds(j or {})
        return await _edit_message_and_send_rest(
            message=progress_message,
            interaction=interaction,
            embeds=business_error_embeds,
            ephemeral=final_ephemeral,
        )

    discord_message = (j or {}).get("discord_message", "")
    llm_payload = (j or {}).get("llm_payload")
    riot_id = (j or {}).get("riotId", "")

    raw_story = await asyncio.to_thread(_generate_story_from_payload, llm_payload)

    summary_embeds = _build_summary_embeds(
        riot_id=riot_id,
        discord_message=discord_message,
    )
    story_embeds = _build_story_embeds(raw_story)

    all_embeds = summary_embeds + story_embeds

    return await _edit_message_and_send_rest(
        message=progress_message,
        interaction=interaction,
        embeds=all_embeds,
        ephemeral=final_ephemeral,
    )
