from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import discord

PostJsonFunc = Callable[[str, dict, int], Awaitable[tuple[int, dict, str]]]
EnsureLinkedFunc = Callable[[discord.Interaction], Awaitable[bool]]


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


def _build_recent_embed(me_json: dict, matches_json: dict, refreshed: bool):
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


async def run_recent_matches(
    *,
    interaction: discord.Interaction,
    post_json: PostJsonFunc,
    ensure_linked_or_prompt: Callable[..., Awaitable[bool]],
    internal_api_key: str | None,
    final_ephemeral: bool,
):
    if not internal_api_key:
        return await interaction.followup.send("サーバー設定エラー: INTERNAL_API_KEY未設定", ephemeral=True)

    linked = await ensure_linked_or_prompt(interaction, use_followup=True)
    if not linked:
        return

    me_status, me_j, _ = await post_json(
        "/internal/rso/me",
        {"discord_user_id": str(interaction.user.id)},
        15,
    )

    if me_status != 200:
        return await interaction.followup.send(f"エラー(me): {me_status} {me_j}", ephemeral=True)

    refreshed = bool(me_j.get("refreshed"))

    rm_status, rm_j, _ = await post_json(
        "/internal/val/recent-matches",
        {"discord_user_id": str(interaction.user.id), "count": 5},
        25,
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

    embed = _build_recent_embed(me_j, rm_j, refreshed)
    return await interaction.followup.send(embed=embed, ephemeral=final_ephemeral)
