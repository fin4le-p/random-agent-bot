# commands/riotcon.py
import asyncio
import json
import os

import aiohttp
import discord
from discord import app_commands

from core.interaction_utils import ExpiringOwnerView
from riotapi import run_match_highlight

API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000").rstrip("/")
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY")


class LinkView(discord.ui.View):
    def __init__(self, url: str):
        super().__init__(timeout=300)
        self.add_item(
            discord.ui.Button(
                label="Riotにログインして連携",
                url=url,
                style=discord.ButtonStyle.link,
            )
        )


def _format_api_error(status_code: int, json_body: dict, raw_text: str) -> str:
    message = (json_body or {}).get("message")
    error_code = (json_body or {}).get("error")

    parts = [f"status={status_code}"]
    if error_code:
        parts.append(f"error={error_code}")
    if message:
        parts.append(str(message))
    elif raw_text:
        parts.append(raw_text[:300])
    else:
        parts.append("API応答の取得に失敗しました。")

    return "\n".join(parts)


async def _post_json(path: str, payload: dict, timeout_sec: int = 15) -> tuple[int, dict, str]:
    if not INTERNAL_API_KEY:
        raise RuntimeError("INTERNAL_API_KEY未設定")

    try:
        timeout = aiohttp.ClientTimeout(total=timeout_sec)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{API_BASE_URL}{path}",
                json=payload,
                headers={"X-Internal-API-Key": INTERNAL_API_KEY},
            ) as resp:
                text = await resp.text()
                try:
                    j = json.loads(text) if text else {}
                except json.JSONDecodeError:
                    j = {"raw": text[:300]}
                return resp.status, j, text

    except asyncio.TimeoutError:
        return (
            503,
            {
                "error": "internal_api_timeout",
                "message": "内部APIがタイムアウトしました。",
                "path": path,
            },
            "",
        )

    except aiohttp.ClientError:
        return (
            503,
            {
                "error": "internal_api_unreachable",
                "message": "内部APIに接続できませんでした。",
                "path": path,
            },
            "",
        )

    except Exception:
        return (
            500,
            {
                "error": "internal_api_unknown_error",
                "message": "内部API呼び出し中に予期しないエラーが発生しました。",
                "path": path,
            },
            "",
        )


async def _send_link_prompt(interaction: discord.Interaction, *, use_followup: bool = False):
    payload = {
        "discord_user_id": str(interaction.user.id),
        "discord_guild_id": str(interaction.guild.id) if interaction.guild else None,
        "region": "ap",
    }

    status_code, j, text = await _post_json("/internal/rso/create-auth-url", payload, timeout_sec=10)
    if status_code != 200:
        msg = f"連携開始に失敗しました。\n{_format_api_error(status_code, j, text)}"
        if use_followup:
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return

    if use_followup:
        await interaction.followup.send(
            "↓このボタンからRiotにログインして連携してください。",
            view=LinkView(j["authorize_url"]),
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            "↓このボタンからRiotにログインして連携してください。",
            view=LinkView(j["authorize_url"]),
            ephemeral=True,
        )


def _build_linked_status_text(status_json: dict) -> str:
    msg = "✅ 連携してるRiotID"
    gn = status_json.get("riot_game_name") or ""
    tl = status_json.get("riot_tag_line") or ""
    if gn and tl:
        msg += f"\n{gn}#{tl}"
    else:
        msg += "\nUnknown"
    return msg


class RiotConMenuView(ExpiringOwnerView):
    def __init__(self, owner_id: int):
        super().__init__(
            owner_id=owner_id,
            timeout=300,
            delete_on_use=True,
            delete_on_timeout=True,
        )

    @discord.ui.button(label="直前の試合ハイライト", style=discord.ButtonStyle.primary)
    async def match_highlight_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await run_match_highlight(
            interaction=interaction,
            post_json=_post_json,
            internal_api_key=INTERNAL_API_KEY,
            final_ephemeral=False,
        )


@app_commands.command(name="riotcon", description="Riot連携メニュー")
async def riotcon_command(interaction: discord.Interaction):
    if not INTERNAL_API_KEY:
        return await interaction.response.send_message("サーバー設定エラー: INTERNAL_API_KEY未設定", ephemeral=True)

    status_code, j, text = await _post_json(
        "/internal/rso/status",
        {"discord_user_id": str(interaction.user.id)},
        timeout_sec=10,
    )

    if status_code != 200:
        return await interaction.response.send_message(
            f"エラー:\n{_format_api_error(status_code, j, text)}",
            ephemeral=True,
        )

    if not j.get("linked"):
        return await _send_link_prompt(interaction, use_followup=False)

    embed = discord.Embed(
        title="Riot連携機能",
        description=_build_linked_status_text(j),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="直近一試合のハイライト",
        value="試合サマリーを整形しストーリーを表示します。（コンペのみ）※この機能は開発段階です",
        inline=False,
    )

    view = RiotConMenuView(owner_id=interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    await view.bind_to_response(interaction)
