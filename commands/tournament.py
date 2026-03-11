import discord
from discord import app_commands


@app_commands.command(name="tournament", description="大会やカスタムの進行ツール")
async def tournament_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Help",
        description="利用可能なコマンド一覧です。",
        color=discord.Color.blurple(),
    )
    view = discord.ui.View()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=False)
