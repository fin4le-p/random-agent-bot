import os
import json
import random
import discord
from discord import app_commands

from views import AgentSelectViewJa
from agents_data import (
    get_default_agents,
    get_chaos_agents,
    get_hirano_agents,
    get_ban_agents,
)

va_group = app_commands.Group(
    name="va",
    description="VALORANT 用エージェント＆パーティツール",
)

# ===== JSON ファイルパス（必要なら .env で上書きできるように） =====

MAP_FILE = os.getenv("MAP_FILE", "maps.json")
PUNISH_FILE = os.getenv("PUNISH_FILE", "punishments.json")


# ===== 共通ヘルパー =====

def _load_json_list(path: str, key: str) -> list[str]:
    """指定キーの配列を JSON から読み込む。失敗したら空配列。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get(key, [])
        return [str(x) for x in items if isinstance(x, str)]
    except Exception as e:
        print(f"Failed to load {key} from {path}: {e}")
        return []


# ===== 既存：エージェントランダム =====

@va_group.command(name="random", description="ランダムでエージェント構成を決めます。")
async def random_cmd(interaction: discord.Interaction):
    await interaction.response.defer()

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

    await interaction.followup.send(
        embed=embed,
        view=AgentSelectViewJa(),
        ephemeral=False,
    )


# ===== ② マップランダム =====

@va_group.command(name="random_map", description="ランダムでマップを1つ選びます。")
async def random_map_cmd(interaction: discord.Interaction):
    await interaction.response.defer()

    maps = _load_json_list(MAP_FILE, "maps")
    if not maps:
        await interaction.followup.send("マップ一覧が空、または読み込みに失敗しました。`maps.json` を確認してください。")
        return

    chosen = random.choice(maps)

    embed = discord.Embed(
        title="本日のマップは…",
        description=f"🎲 ランダムに選ばれたマップは **{chosen}** です！",
        color=discord.Color.green(),
    )

    await interaction.followup.send(embed=embed)


# ===== ③ ピック禁止祭（BAN ルーレット） =====

@va_group.command(name="ban", description="ピック禁止エージェントをランダムで決めます。")
@app_commands.describe(count="BAN するエージェント数（1〜5、未指定は2）")
async def ban_cmd(interaction: discord.Interaction, count: int | None = 2):
    await interaction.response.defer()

    if count is None:
        count = 2
    count = max(1, min(count, 5))

    banned = get_ban_agents(count)
    if not banned:
        await interaction.followup.send("エージェント一覧が空です。`agents.json` を確認してください。")
        return

    banned_list = "\n".join(f"- {name}" for name in banned)

    embed = discord.Embed(
        title="ピック禁止祭（BAN ルーレット）",
        description=f"この試合で **ピック禁止** になったエージェントは：\n\n{banned_list}",
        color=discord.Color.red(),
    )
    embed.set_footer(text="※ 罰ゲーム用のローカルルールなどと組み合わせて使ってね。")

    await interaction.followup.send(embed=embed)


# ===== ⑤ 罰ゲームルーレット（VC全員・一人ずつ違う） =====

@va_group.command(name="punish", description="VCメンバー全員に罰ゲームを割り当てます。")
async def punish_cmd(interaction: discord.Interaction):
    await interaction.response.defer()

    if not (interaction.user.voice and interaction.user.voice.channel):
        await interaction.followup.send("VC に参加してから `/va punish` を実行してください。")
        return

    members = [m for m in interaction.user.voice.channel.members if not m.bot]
    if not members:
        await interaction.followup.send("VC に人がいません。（Bot は除外しています）")
        return

    punish_list = _load_json_list(PUNISH_FILE, "punishments")
    if not punish_list:
        await interaction.followup.send("罰ゲームリストが空、または読み込みに失敗しました。`punishments.json` を確認してください。")
        return

    # メンバー人数に応じて罰ゲームを用意する
    random.shuffle(punish_list)

    if len(punish_list) >= len(members):
        selected = random.sample(punish_list, len(members))
    else:
        # 足りない場合はローテーションして被りを許容
        selected = []
        idx = 0
        while len(selected) < len(members):
            selected.append(punish_list[idx % len(punish_list)])
            idx += 1

    embed = discord.Embed(
        title="罰ゲームルーレット",
        description="VC にいる全員に罰ゲームを割り当てました。",
        color=discord.Color.purple(),
    )

    for member, punish in zip(members, selected):
        embed.add_field(name=member.display_name, value=punish, inline=False)

    await interaction.followup.send(embed=embed)


# ===== ⑥ 役職シャッフル =====

@va_group.command(name="role_shuffle", description="VCメンバーに5つの役職を割り当てます。")
async def role_shuffle_cmd(interaction: discord.Interaction):
    await interaction.response.defer()

    # VC チェック
    if not (interaction.user.voice and interaction.user.voice.channel):
        await interaction.followup.send("VC に参加してから `/va role_shuffle` を実行してください。")
        return

    # Bot を除外
    members = [m for m in interaction.user.voice.channel.members if not m.bot]
    if not members:
        await interaction.followup.send("VC に人がいません。（Bot は除外しています）")
        return

    # 5つの固定ロール（順番も意味を持たせる）
    role_defs = [
        {
            "title": "IGL（作戦コール担当）",
            "sentence": "この試合のIGLは **{name}** です！ 全ラウンドの作戦コールをお願いします。"
        },
        {
            "title": "エントリー担当",
            "sentence": "この試合のエントリー担当は **{name}** です！ サイトに入る先頭をお願いします。"
        },
        {
            "title": "スパイク担当",
            "sentence": "この試合のスパイク担当は **{name}** です！ スパイクの管理と設置をお願いします。"
        },
        {
            "title": "オペレーター担当",
            "sentence": "この試合のオペレーター担当は **{name}** です！ お金に余裕があるラウンドではオペを優先してください。"
        },
        {
            "title": "情報共有係",
            "sentence": "この試合の情報共有係は **{name}** です！ 敵位置や音の情報を積極的にコールしてください。"
        },
    ]

    random.shuffle(members)  # 誰にどの役職が行くかシャッフル

    embed = discord.Embed(
        title="役職シャッフル",
        description="この試合の役職担当は以下の通りです！",
        color=discord.Color.blue(),
    )

    # 5つの役職を、最大5人まで被りなしで割り当て
    max_roles = min(5, len(members))
    for i in range(max_roles):
        member = members[i]
        role_def = role_defs[i]
        text = role_def["sentence"].format(name=member.display_name)
        embed.add_field(name=role_def["title"], value=text, inline=False)

    # 6人目以降は「役職なし（自由枠）」
    if len(members) > 5:
        for member in members[5:]:
            embed.add_field(
                name=member.display_name,
                value="この試合は役職なし（自由枠）です。好きに暴れてください。",
                inline=False,
            )

    await interaction.followup.send(embed=embed)


# ===== ⑧ チーム分けランダム（2チーム） =====

@va_group.command(name="teams", description="VCメンバーを2チームにランダムで分けます。")
async def teams_cmd(interaction: discord.Interaction):
    await interaction.response.defer()

    if not (interaction.user.voice and interaction.user.voice.channel):
        await interaction.followup.send("VC に参加してから `/va teams` を実行してください。")
        return

    members = [m for m in interaction.user.voice.channel.members if not m.bot]
    if len(members) < 2:
        await interaction.followup.send("チーム分けするには最低 2 人必要です。")
        return

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

    await interaction.followup.send(embed=embed)


# ===== ヘルプ =====

@va_group.command(name="help", description="VA Bot のヘルプを表示します。")
async def help_cmd(interaction: discord.Interaction):
    await interaction.response.defer()

    text = (
        "**/va random**\n"
        "エージェント構成をモード別にランダム生成します。\n\n"
        "**/va random_map**\n"
        "マップをランダムで 1 つ選びます。\n\n"
        "**/va ban [count]**\n"
        "ピック禁止エージェントをランダムで決めます。\n\n"
        "**/va punish**\n"
        "VC にいるメンバー全員に、それぞれ別の罰ゲームを割り当てます。\n\n"
        "**/va role_shuffle**\n"
        "VC メンバーに役職をランダムで割り当てます。\n\n"
        "**/va teams**\n"
        "VC メンバーを 2 チームにランダムで分けます。\n"
    )

    await interaction.followup.send(text)

    embed = discord.Embed(
        description="HP: [ヴァロラント ランダムエージェント](https://random-agent.nakano6.com)",
        color=discord.Color.blue(),
    )
    await interaction.followup.send(embed=embed)
