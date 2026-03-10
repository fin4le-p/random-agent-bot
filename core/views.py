import random

import discord

from core.agents_data import get_chaos_agents, get_default_agents, get_hirano_agents
from core.interaction_utils import ExpiringOwnerView


class AgentSelectJa(discord.ui.Button):
    def __init__(self, label: str, value: str, parent_view: "AgentSelectViewJa"):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.value = value
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

        agents: list[str] = []
        mode_title = ""
        color = discord.Color.default()

        if interaction.user.voice and interaction.user.voice.channel:
            members = [member for member in interaction.user.voice.channel.members if not member.bot]
            picked_members = (
                random.sample(members, k=min(5, len(members)))
                if members
                else []
            )
            user_names = [member.display_name for member in picked_members]
        else:
            user_names = []

        while len(user_names) < 5:
            user_names.append(f"Player{len(user_names) + 1}")

        if self.value == "1":
            agents = get_default_agents()
            mode_title = "デフォルトモード"
            color = discord.Color.blue()
        elif self.value == "2":
            agents = get_chaos_agents()
            mode_title = "カオスモード"
            color = discord.Color.red()
        elif self.value == "3":
            agents = get_hirano_agents()
            mode_title = "平野流モード"
            color = discord.Color.orange()
        else:
            await interaction.followup.send("無効なモードが選択されました。", ephemeral=True)
            return

        if not agents:
            await interaction.followup.send(
                "エージェント一覧の読み込みに失敗したか、モードの条件を満たせませんでした。`agents.json` を確認してください。",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=mode_title,
            description=(
                "ボイスチャンネルに 5 人以上いる場合は、**Bot を除いた中からランダムで 5 人** を割り当てます。\n"
                "見る専の人がいる場合は、見る専の人が自分で指名してあげましょう！"
            ),
            color=color,
        )

        for i, agent_name in enumerate(agents, start=1):
            player_name = user_names[i - 1]
            embed.add_field(name=player_name, value=agent_name, inline=False)

        embed.set_footer(text="注意：この構成は試合に勝つことを前提とした構成ではありません。")

        await self.parent_view.disable_and_stop(embed=embed)


class AgentSelectViewJa(ExpiringOwnerView):
    def __init__(self, owner_id: int, timeout: float | None = 180):
        super().__init__(
            owner_id=owner_id,
            timeout=timeout,
            single_use=False,
            delete_on_use=False,
            delete_on_timeout=True,
        )
        self.add_item(AgentSelectJa("デフォルト", "1", self))
        self.add_item(AgentSelectJa("カオス", "2", self))
        self.add_item(AgentSelectJa("平野流", "3", self))