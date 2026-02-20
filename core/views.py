import discord

from core.agents_data import get_default_agents, get_chaos_agents, get_hirano_agents
from core.interaction_utils import ExpiringOwnerView


class AgentSelectJa(discord.ui.Button):
    def __init__(self, label: str, value: str, parent_view: "AgentSelectViewJa"):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.value = value
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        agents = []
        mode_title = ""
        color = discord.Color.default()

        if interaction.user.voice and interaction.user.voice.channel:
            channel = interaction.user.voice.channel
            members = channel.members
            user_names = [member.display_name for member in members][:5]
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
            await interaction.response.send_message("無効なモードが選択されました。", ephemeral=True)
            return

        embed = discord.Embed(
            title=mode_title,
            description=(
                "ボイスチャンネルに 5 人以上いる場合は、その中から 5 人が自動で割り当てられます。\n"
                "見る専の人がいる場合は、見る専の人が自分で指名してあげましょう！"
            ),
            color=color,
        )

        for i, agent_name in enumerate(agents, start=1):
            player_name = user_names[i - 1]
            embed.add_field(name=player_name, value=agent_name, inline=False)

        embed.set_footer(text="注意：この構成は試合に勝つことを前提とした構成ではありません。")
        self.parent_view._disable_children()
        await interaction.response.edit_message(embed=embed, view=self.parent_view)
        self.parent_view.stop()


class AgentSelectViewJa(ExpiringOwnerView):
    def __init__(self, owner_id: int, timeout: float | None = 180):
        super().__init__(owner_id=owner_id, timeout=timeout)
        self.add_item(AgentSelectJa("デフォルト", "1", self))
        self.add_item(AgentSelectJa("カオス", "2", self))
        self.add_item(AgentSelectJa("平野流", "3", self))
