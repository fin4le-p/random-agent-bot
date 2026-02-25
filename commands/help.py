import discord
from discord import app_commands


@app_commands.command(name="help", description="使えるコマンド一覧を表示します。")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Help",
        description="利用可能なコマンド一覧です。",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="/random",
        value="ランダム機能メニュー（Agent / Map / Role Shuffle / Teams / BAN）",
        inline=False,
    )
    embed.add_field(
        name="/punish",
        value="罰ゲームメニュー（VC罰ゲーム / AI罰ゲーム）",
        inline=False,
    )
    embed.add_field(
        name="/tactic",
        value="AI戦術メニュー（通常 / ハード）",
        inline=False,
    )
    embed.set_footer(text="各コマンドはスラッシュで実行してください。")
    view = discord.ui.View()
    view.add_item(
        discord.ui.Button(
            label="詳細はHPをご確認ください",
            url="https://random-agent.nakano6.com/",
        )
    )
    await interaction.response.send_message(embed=embed, view=view, ephemeral=False)
