import discord
from discord import app_commands

from core.ai_engine import generate
from core.interaction_utils import ExpiringOwnerView

MODEL_LABELS = {
    1: "ID1: 早いが不安定（llama）",
    2: "ID2: 速くてやや高品質（gpt-oss）",
    3: "ID3: 遅いが安定（gpt）",
}


class TacticModelSelectView(ExpiringOwnerView):
    def __init__(self, owner_id: int, content: str, hard: bool):
        super().__init__(owner_id=owner_id, timeout=300)
        self.content = content
        self.hard = hard

    async def _run(self, interaction: discord.Interaction, model_id: int):
        await interaction.response.defer(thinking=True)
        try:
            result = generate("tactic", self.hard, model_id, self.content)
        except Exception as exc:
            return await interaction.followup.send(f"エラー: {exc}")
        title = "🧠 Tactic(HARD)" if self.hard else "🧠 Tactic"
        embed = discord.Embed(title=title, description=result, color=discord.Color.dark_green())
        embed.set_footer(text=f"model={model_id} ({MODEL_LABELS[model_id]})")
        self._disable_children()
        try:
            if interaction.message:
                await interaction.message.edit(view=self)
        except Exception:
            pass
        self.stop()
        await interaction.followup.send(embed=embed)

    @discord.ui.button(label="1", style=discord.ButtonStyle.secondary)
    async def model_1(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._run(interaction, 1)

    @discord.ui.button(label="2", style=discord.ButtonStyle.primary)
    async def model_2(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._run(interaction, 2)

    @discord.ui.button(label="3", style=discord.ButtonStyle.success)
    async def model_3(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._run(interaction, 3)


class TacticContentModal(discord.ui.Modal, title="戦術の内容を入力"):
    content = discord.ui.TextInput(
        label="内容",
        placeholder="例: バインド攻めで、相手にオペが出てきてから連敗中",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=400,
    )

    def __init__(self, owner_id: int, hard: bool):
        super().__init__()
        self.owner_id = owner_id
        self.hard = hard

    async def on_submit(self, interaction: discord.Interaction):
        mode_text = "HARD" if self.hard else "通常"
        embed = discord.Embed(
            title=f"tactic: {mode_text}",
            description="モデルIDを選択してください（1/2/3）。",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="入力内容", value=str(self.content), inline=False)
        model_lines = "\n".join(f"{k}. {v}" for k, v in MODEL_LABELS.items())
        embed.add_field(name="モデル一覧", value=model_lines, inline=False)
        view = TacticModelSelectView(owner_id=self.owner_id, content=str(self.content), hard=self.hard)
        await interaction.response.send_message(embed=embed, view=view)
        await view.bind_to_response(interaction)


class TacticModeView(ExpiringOwnerView):
    def __init__(self, owner_id: int):
        super().__init__(owner_id=owner_id, timeout=300)

    @discord.ui.button(label="通常", style=discord.ButtonStyle.primary)
    async def normal_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(TacticContentModal(owner_id=interaction.user.id, hard=False))

    @discord.ui.button(label="ハード", style=discord.ButtonStyle.danger)
    async def hard_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(TacticContentModal(owner_id=interaction.user.id, hard=True))


@app_commands.command(name="tactic", description="戦術生成メニューを表示します。")
async def tactic_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🧠 tactic",
        description="通常 or ハードを選択すると、内容入力→モデルID選択で生成します。",
        color=discord.Color.blurple(),
    )
    view = TacticModeView(owner_id=interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view)
    await view.bind_to_response(interaction)
