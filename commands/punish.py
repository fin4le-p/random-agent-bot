import json
import os
import random

import discord
from discord import app_commands

from core.ai_engine import generate
from core.interaction_utils import ExpiringOwnerView, bot_add_prompt_text, is_bot_member_in_guild

PUNISH_FILE = os.getenv("PUNISH_FILE", "punishments.json")

MODEL_LABELS = {
    1: "ID1: 早いが不安定（llama）",
    2: "ID2: 速くてやや高品質（gpt-oss）",
    3: "ID3: 遅いが安定（gpt）",
}


def _load_json_list(path: str, key: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get(key, [])
        return [str(x) for x in items if isinstance(x, str)]
    except Exception as e:
        print(f"Failed to load {key} from {path}: {e}")
        return []


def _build_vc_punish_embed(members: list[discord.Member]) -> discord.Embed | None:
    punish_list = _load_json_list(PUNISH_FILE, "punishments")
    if not punish_list:
        return None

    random.shuffle(punish_list)
    if len(punish_list) >= len(members):
        selected = random.sample(punish_list, len(members))
    else:
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
    return embed


class PunishModelSelectView(ExpiringOwnerView):
    def __init__(self, owner_id: int, content: str, hard: bool):
        super().__init__(
            owner_id=owner_id,
            timeout=300,
            delete_on_use=True,
            delete_on_timeout=True,
        )
        self.content = content
        self.hard = hard

    async def _run(self, interaction: discord.Interaction, model_id: int):
        await interaction.response.defer(thinking=True)
        try:
            result = generate("punish", self.hard, model_id, self.content)
        except Exception as exc:
            return await interaction.followup.send(f"エラー: {exc}")
        title = "💥 Punish AI(HARD)" if self.hard else "💥 Punish AI"
        embed = discord.Embed(title=title, description=result, color=discord.Color.dark_orange())
        embed.set_footer(text=f"model={model_id}")
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


class PunishContentModal(discord.ui.Modal, title="罰ゲームの内容を入力"):
    content = discord.ui.TextInput(
        label="内容",
        placeholder="例: ファーストデスして、相手にオペ拾われました",
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
            title=f"punish(ai): {mode_text}",
            description="モデルIDを選択してください（1/2/3）。",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="入力内容", value=str(self.content), inline=False)
        model_lines = "\n".join(f"{k}. {v}" for k, v in MODEL_LABELS.items())
        embed.add_field(name="モデル一覧", value=model_lines, inline=False)
        view = PunishModelSelectView(owner_id=self.owner_id, content=str(self.content), hard=self.hard)
        await interaction.response.send_message(embed=embed, view=view)
        await view.bind_to_response(interaction)


class PunishMenuView(ExpiringOwnerView):
    def __init__(self, owner_id: int):
        super().__init__(
            owner_id=owner_id,
            timeout=300,
            delete_on_use=True,
            delete_on_timeout=True,
        )

    @discord.ui.button(label="VC罰ゲーム", style=discord.ButtonStyle.primary)
    async def vc_button(self, interaction: discord.Interaction, _: discord.ui.Button):
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
        embed = _build_vc_punish_embed(members)
        if not embed:
            return await interaction.response.send_message(
                "罰ゲームリストが空、または読み込みに失敗しました。`punishments.json` を確認してください。",
                ephemeral=True,
            )
        await interaction.response.send_message(embed=embed)

    @discord.ui.button(label="AI罰ゲーム", style=discord.ButtonStyle.secondary)
    async def ai_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(PunishContentModal(owner_id=interaction.user.id, hard=False))

    @discord.ui.button(label="AI罰ゲーム(HARD)", style=discord.ButtonStyle.danger)
    async def ai_hard_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(PunishContentModal(owner_id=interaction.user.id, hard=True))


@app_commands.command(name="punish", description="罰ゲームメニューを表示します。")
async def punish_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="💥 punish",
        description=(
            "VC罰ゲーム or AI罰ゲームを選択してください。\n"
            "AIは内容入力後にモデルID(1/2/3)を選択します。"
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="注意",
        value="Appのみ追加の状態だとVC罰ゲームは使えません。Botとしてサーバーに追加してください。",
        inline=False,
    )
    view = PunishMenuView(owner_id=interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view)
    await view.bind_to_response(interaction)
