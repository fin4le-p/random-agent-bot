import os
import discord
from discord import app_commands
import aiohttp
from datetime import datetime, timezone

from core.interaction_utils import ExpiringOwnerView

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
    msg = "✅ 連携RiotID"
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
        await _run_recent_matches(interaction, final_ephemeral=False)


def _fmt_map(name: str) -> str:
    if not name:
        return "Unknown"
    return name


def _fmt_result(won):
    if won is True:
        return "✅ WIN"
    if won is False:
        return "❌ LOSE"
    return "❔"


def _ms_to_dt(ms):
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except Exception:
        return None


def build_recent_embed(me_json: dict, matches_json: dict, refreshed: bool):
    gn = me_json.get("game_name") or ""
    tl = me_json.get("tag_line") or ""
    riot_id = f"{gn}#{tl}".strip("#") if (gn and tl) else "Unknown"
    puuid = me_json.get("puuid") or matches_json.get("puuid") or ""

    e = discord.Embed(
        title="🎮 VALORANT 直近試合",
        description=f"**{riot_id}**\nPUUID: `{puuid[:8]}...`" if puuid else f"**{riot_id}**",
        color=0x5865F2,
    )

    if refreshed:
        e.add_field(name="Token", value="期限切れ → refresh 済み", inline=True)
    else:
        e.add_field(name="Token", value="OK", inline=True)

    region = matches_json.get("region") or "?"
    e.add_field(name="Region", value=f"`{region}`", inline=True)

    matches = matches_json.get("matches") or []
    if not matches:
        e.add_field(
            name="Matches",
            value="試合が見つからない/取得できませんでした（VAL-MATCH API or PUUID を確認）",
            inline=False,
        )
        return e

    lines = []
    latest_dt = None

    for i, m in enumerate(matches, start=1):
        result = _fmt_result(m.get("won"))
        map_name = _fmt_map(m.get("map", ""))
        mode = m.get("mode") or ""
        k = m.get("k", 0)
        d = m.get("d", 0)
        a = m.get("a", 0)
        acs = m.get("acs", "-")
        mid = m.get("matchId", "")

        dt = _ms_to_dt(m.get("gameStartMillis"))
        if dt and (latest_dt is None or dt > latest_dt):
            latest_dt = dt

        lines.append(
            f"**{i}. {result}**  `{map_name}`  ({mode})\n"
            f"KDA **{k}/{d}/{a}**  |  ACS **{acs}**\n"
            f"`{mid}`"
        )

    e.add_field(name="Matches", value="\n\n".join(lines), inline=False)

    if latest_dt:
        e.set_footer(text=f"latest: {latest_dt.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    return e


async def _run_recent_matches(interaction: discord.Interaction, *, final_ephemeral: bool):
    if not INTERNAL_API_KEY:
        return await interaction.followup.send("サーバー設定エラー: INTERNAL_API_KEY未設定", ephemeral=True)

    linked = await _ensure_linked_or_prompt(interaction, use_followup=True)
    if not linked:
        return

    me_status, me_j, _ = await _post_json(
        "/internal/rso/me",
        {"discord_user_id": str(interaction.user.id)},
        timeout_sec=15,
    )

    if me_status != 200:
        return await interaction.followup.send(f"エラー(me): {me_status} {me_j}", ephemeral=True)

    refreshed = bool(me_j.get("refreshed"))

    rm_status, rm_j, _ = await _post_json(
        "/internal/val/recent-matches",
        {"discord_user_id": str(interaction.user.id), "count": 5},
        timeout_sec=25,
    )

    if rm_status == 404 and rm_j.get("error") in (
        "not_linked",
        "not_linked_or_missing_puuid",
        "not_linked_or_missing_riot_id",
    ):
        gn = me_j.get("game_name") or ""
        tl = me_j.get("tag_line") or ""
        riot_id = f"{gn}#{tl}".strip("#") if (gn and tl) else f"subject={me_j.get('riot_subject','')}"
        msg = f"✅ Riot疎通OK: `{riot_id}`\n（直近試合は未取得: {rm_j}）"
        return await interaction.followup.send(msg, ephemeral=final_ephemeral)

    if rm_status != 200:
        return await interaction.followup.send(f"エラー(recent-matches): {rm_status} {rm_j}", ephemeral=True)

    embed = build_recent_embed(me_j, rm_j, refreshed)
    return await interaction.followup.send(embed=embed, ephemeral=final_ephemeral)


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

    view = RiotConMenuView(owner_id=interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    await view.bind_to_response(interaction)
