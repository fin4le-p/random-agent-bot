import json
import logging
from typing import Awaitable, Callable

import discord

PostJsonFunc = Callable[[str, dict, int], Awaitable[tuple[int, dict, str]]]
logger = logging.getLogger(__name__)


def _format_raw_response(status_code: int, json_body: dict, raw_text: str) -> str:
    if json_body and "raw" not in json_body:
        body_text = json.dumps(json_body, ensure_ascii=False)
    else:
        body_text = raw_text or json.dumps(json_body, ensure_ascii=False)
    return f"status={status_code}\n{body_text}"


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

    output = _format_raw_response(status_code, j, text)
    logger.info("match-highlight full response:\n%s", output)
    wrapped = f"```json\n{output}\n```"
    if len(wrapped) <= 1900:
        return await interaction.followup.send(wrapped, ephemeral=final_ephemeral)

    truncated = output[:1700] + "\n...（長いため省略）"
    return await interaction.followup.send(f"```json\n{truncated}\n```", ephemeral=final_ephemeral)
