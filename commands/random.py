import json
import os
import random

import discord
from discord import app_commands

from core.agents_data import get_ban_agents
from core.interaction_utils import ExpiringOwnerView, bot_add_prompt_text, is_bot_member_in_guild
from core.views import AgentSelectViewJa

MAP_FILE = os.getenv("MAP_FILE", "maps.json")


def _load_json_list(path: str, key: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get(key, [])
        return [str(x) for x in items if isinstance(x, str)]
    except Exception as e:
        print(f"Failed to load {key} from {path}: {e}")
        return []


def _random_map_embed() -> discord.Embed | None:
    maps = _load_json_list(MAP_FILE, "maps")
    if not maps:
        return None
    chosen = random.choice(maps)
    return discord.Embed(
        title="本日のマップは…",
        description=f"🎲 ランダムに選ばれたマップは **{chosen}** です！",
        color=discord.Color.green(),
    )


def _role_shuffle_embed(members: list[discord.Member]) -> discord.Embed:
    role_defs = [
        {
            "title": "IGL（作戦コール担当）",
            "sentence": "この試合のIGLは **{name}** です！ 全ラウンドの作戦コールをお願いします。",
        },
        {
            "title": "エントリー担当",
            "sentence": "この試合のエントリー担当は **{name}** です！ サイトに入る先頭をお願いします。",
        },
        {
            "title": "スパイク担当",
            "sentence": "この試合のスパイク担当は **{name}** です！ スパイクの管理と設置をお願いします。",
        },
        {
            "title": "オペレーター担当",
            "sentence": "この試合のオペレーター担当は **{name}** です！ お金に余裕があるラウンドではオペを優先してください。",
        },
        {
            "title": "情報共有係",
            "sentence": "この試合の情報共有係は **{name}** です！ 敵位置や音の情報を積極的にコールしてください。",
        },
    ]

    random.shuffle(members)
    embed = discord.Embed(
        title="役職シャッフル",
        description="この試合の役職担当は以下の通りです！",
        color=discord.Color.blue(),
    )
    max_roles = min(5, len(members))
    for i in range(max_roles):
        member = members[i]
        role_def = role_defs[i]
        embed.add_field(
            name=role_def["title"],
            value=role_def["sentence"].format(name=member.display_name),
            inline=False,
        )
    if len(members) > 5:
        for member in members[5:]:
            embed.add_field(
                name=member.display_name,
                value="この試合は役職なし（自由枠）です。好きに暴れてください。",
                inline=False,
            )
    return embed


def _teams_embed(members: list[discord.Member]) -> discord.Embed:
    random.shuffle(members)
    mid = len(members) // 2
    team_a = members[:mid]
    team_b = members[mid:]
    embed = discord.Embed(
        title="チーム分けランダム",
        description="VC メンバーを 2 チームにランダムで分けました。",
        color=discord.Color.teal(),
    )

    def format_team(team_members: list[discord.Member]) -> str:
        if not team_members:
            return "（なし）"
        return "\n".join(f"- {m.display_name}" for m in team_members)

    embed.add_field(name="チームA", value=format_team(team_a), inline=True)
    embed.add_field(name="チームB", value=format_team(team_b), inline=True)
    return embed


class RandomMenuView(ExpiringOwnerView):
    def __init__(self, owner_id: int):
        super().__init__(
            owner_id=owner_id,
            timeout=300,
            delete_on_use=True,
            delete_on_timeout=True,
        )

    @discord.ui.button(label="Agent", style=discord.ButtonStyle.primary)
    async def agent_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        embed = discord.Embed(
            title="モードを選択してください",
            description=(
                "**デフォルト**:\n"
                "各ロールから 1 人ずつ + フリー枠 1 人の合計 5 人が選ばれます。\n\n"
                "**カオス**:\n"
                "ロールを完全に無視して、全エージェントから 5 人ランダムで選びます。\n\n"
                "**平野流**:\n"
                "必ずコントローラーが 1 人以上含まれるように、5 人がランダムで選ばれます。\n"
            ),
            color=discord.Color.blue(),
        )
        view = AgentSelectViewJa(owner_id=interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view)
        await view.bind_to_response(interaction)

    @discord.ui.button(label="Map", style=discord.ButtonStyle.success)
    async def map_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        embed = _random_map_embed()
        if not embed:
            return await interaction.response.send_message(
                "マップ一覧が空、または読み込みに失敗しました。`maps.json` を確認してください。"
            )
        await interaction.response.send_message(embed=embed)

    @discord.ui.button(label="Role Shuffle", style=discord.ButtonStyle.secondary)
    async def role_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not is_bot_member_in_guild(interaction):
            return await interaction.response.send_message(bot_add_prompt_text(), ephemeral=True)
        if not (interaction.user.voice and interaction.user.voice.channel):
            return await interaction.response.send_message(
                "VC に参加してから実行してください。", ephemeral=True
            )
        members = [m for m in interaction.user.voice.channel.members if not m.bot]
        if not members:
            return await interaction.response.send_message(
                "VC に人がいません。（Bot は除外しています）", ephemeral=True
            )
        await interaction.response.send_message(embed=_role_shuffle_embed(members))

    @discord.ui.button(label="Teams", style=discord.ButtonStyle.secondary)
    async def teams_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not is_bot_member_in_guild(interaction):
            return await interaction.response.send_message(bot_add_prompt_text(), ephemeral=True)
        if not (interaction.user.voice and interaction.user.voice.channel):
            return await interaction.response.send_message(
                "VC に参加してから実行してください。", ephemeral=True
            )
        members = [m for m in interaction.user.voice.channel.members if not m.bot]
        if len(members) < 2:
            return await interaction.response.send_message(
                "チーム分けするには最低 2 人必要です。", ephemeral=True
            )
        await interaction.response.send_message(embed=_teams_embed(members))

    @discord.ui.button(label="BAN", style=discord.ButtonStyle.danger)
    async def ban_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        banned = get_ban_agents(3)
        if not banned:
            return await interaction.response.send_message(
                "エージェント一覧が空です。`agents.json` を確認してください。", ephemeral=True
            )
        banned_list = "\n".join(f"- {name}" for name in banned)
        embed = discord.Embed(
            title="ピック禁止祭（BAN ルーレット）",
            description=f"この試合で **ピック禁止** になったエージェントは：\n\n{banned_list}",
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed)


@app_commands.command(name="random", description="ランダム系機能のメニューを表示します。")
async def random_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎲 Random メニュー",
        description="下のボタンから実行する機能を選択してください。",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Agent", value="エージェント構成をランダム生成します。", inline=False)
    embed.add_field(name="Map", value="マップをランダムで1つ選びます。", inline=False)
    embed.add_field(name="Role Shuffle", value="VCメンバーへ役職をランダム割り当てします。", inline=False)
    embed.add_field(name="Teams", value="VCメンバーを2チームにランダム分けします。", inline=False)
    embed.add_field(name="BAN", value="ピック禁止エージェントをランダムで選びます。", inline=False)
    embed.add_field(
        name="注意",
        value="Appのみ追加の状態だとVC系機能は使えません。Botとしてサーバーに追加してください。",
        inline=False,
    )
    view = RandomMenuView(owner_id=interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view)
    await view.bind_to_response(interaction)
