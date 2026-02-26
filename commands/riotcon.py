# commands/riotcon.py  (あなたが貼ってくれた側)
import os

import aiohttp
import discord
from discord import app_commands

from core.interaction_utils import ExpiringOwnerView
from riotapi import run_match_highlight, run_recent_matches

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


async def _post_json(path: str, payload: dict, timeout_sec: int = 15):
    if not INTERNAL_API_KEY:
        raise RuntimeError("INTERNAL_API_KEY未設定")

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{API_BASE_URL}{path}",
            json=payload,
            headers={"X-Internal-API-Key": INTERNAL_API_KEY},
            timeout=aiohttp.ClientTimeout(total=timeout_sec),
        ) as resp:
            text = await resp.text()
            try:
                j = await resp.json()
            except Exception:
                j = {"raw": text[:300]}
            return resp.status, j, text


async def _send_link_prompt(interaction: discord.Interaction, *, use_followup: bool = False):
    payload = {
        "discord_user_id": str(interaction.user.id),
        "discord_guild_id": str(interaction.guild.id) if interaction.guild else None,
        "region": "ap",
    }

    status_code, j, text = await _post_json("/internal/rso/create-auth-url", payload, timeout_sec=10)
    if status_code != 200:
        msg = f"連携開始に失敗しました: {status_code}\n{text[:300]}"
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


async def _ensure_linked_or_prompt(interaction: discord.Interaction, *, use_followup: bool = False) -> bool:
    status_code, j, _ = await _post_json(
        "/internal/rso/status",
        {"discord_user_id": str(interaction.user.id)},
        timeout_sec=10,
    )

    if status_code != 200:
        msg = f"エラー(status): {status_code} {j}"
        if use_followup:
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return False

    if j.get("linked"):
        return True

    await _send_link_prompt(interaction, use_followup=use_followup)
    return False


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

    @discord.ui.button(label="直近試合", style=discord.ButtonStyle.primary)
    async def recent_matches_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await run_recent_matches(
            interaction=interaction,
            post_json=_post_json,
            ensure_linked_or_prompt=_ensure_linked_or_prompt,
            internal_api_key=INTERNAL_API_KEY,
            final_ephemeral=False,
        )

    @discord.ui.button(label="直前の試合ハイライト", style=discord.ButtonStyle.secondary)
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

    status_code, j, _ = await _post_json(
        "/internal/rso/status",
        {"discord_user_id": str(interaction.user.id)},
        timeout_sec=10,
    )

    if status_code != 200:
        return await interaction.response.send_message(f"エラー(status): {status_code} {j}", ephemeral=True)

    if not j.get("linked"):
        return await _send_link_prompt(interaction, use_followup=False)

    embed = discord.Embed(
        title="Riot連携機能",
        description=_build_linked_status_text(j),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="直近試合", value="Riot疎通確認 + 直近5試合の戦績を表示します。", inline=False)
    embed.add_field(name="直近一試合のハイライト", value="試合サマリーを整形し、LLMで生成したストーリーを表示します。（コンペのみ）", inline=False)

    view = RiotConMenuView(owner_id=interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    await view.bind_to_response(interaction)