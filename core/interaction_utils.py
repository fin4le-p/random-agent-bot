import logging
import os
from typing import Iterable

import discord

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SEC = 180
MAX_EMBED_FIELDS = 25
MAX_EMBED_TOTAL_CHARS = 6000
SAFE_EMBED_TOTAL_CHARS = 5800


def bot_add_prompt_text() -> str:
    invite_url = os.getenv("DISCORD_BOT_INVITE_URL") or (
        "https://discord.com/oauth2/authorize"
        "?client_id=1308611315878858762"
        "&permissions=2147601408"
        "&integration_type=0"
        "&scope=bot+applications.commands"
    )

    return (
        "この機能は **Botとしてサーバーに追加** されていないと使えません。\n"
        f"追加リンク: {invite_url}"
    )


def is_bot_member_in_guild(interaction: discord.Interaction) -> bool:
    guild = interaction.guild
    bot_user = interaction.client.user
    if guild is None or bot_user is None:
        return False
    # discord.py injects a synthetic "self member" for guild interactions, so
    # guild.get_member(bot_user.id) can be truthy even for App Directory installs.
    if hasattr(interaction, "is_guild_integration"):
        return interaction.is_guild_integration()
    return guild.get_member(bot_user.id) is not None


def _estimate_embed_len(title: str | None, description: str | None, footer_text: str | None = None) -> int:
    return len(title or "") + len(description or "") + len(footer_text or "")


def build_embeds_from_fields(
    *,
    title: str,
    description: str | None,
    color: discord.Color,
    fields: Iterable[tuple[str, str, bool]],
    footer_text: str | None = None,
) -> list[discord.Embed]:
    embeds: list[discord.Embed] = []
    current_title = title
    current_description = description
    current = discord.Embed(title=current_title, description=current_description, color=color)
    current_chars = _estimate_embed_len(current_title, current_description, footer_text)
    current_field_count = 0

    for name, value, inline in fields:
        add_chars = len(name) + len(value)
        need_new_embed = (
            current_field_count >= MAX_EMBED_FIELDS
            or current_chars + add_chars > SAFE_EMBED_TOTAL_CHARS
        )

        if need_new_embed:
            embeds.append(current)
            current_title = f"{title}（続き）"
            current_description = None
            current = discord.Embed(title=current_title, description=current_description, color=color)
            current_chars = _estimate_embed_len(current_title, current_description, footer_text)
            current_field_count = 0

        current.add_field(name=name, value=value, inline=inline)
        current_chars += add_chars
        current_field_count += 1

    if current_field_count == 0 and not embeds:
        embeds.append(current)
    elif current_field_count > 0:
        embeds.append(current)

    if footer_text:
        embeds[-1].set_footer(text=footer_text)

    return embeds


class ExpiringOwnerView(discord.ui.View):
    def __init__(
        self,
        owner_id: int | None = None,
        timeout: float | None = DEFAULT_TIMEOUT_SEC,
        single_use: bool = True,
        delete_on_use: bool = False,
        delete_on_timeout: bool = False,
    ):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.message: discord.Message | None = None
        self.single_use = single_use
        self.delete_on_use = delete_on_use
        self.delete_on_timeout = delete_on_timeout
        self._consumed = False

    async def bind_to_response(self, interaction: discord.Interaction) -> None:
        try:
            self.message = await interaction.original_response()
        except Exception:
            logger.exception("Failed to bind view to original_response().")
            self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.owner_id is not None and interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "このボタンはコマンド実行者のみ操作できます。",
                ephemeral=True,
            )
            return False

        if not self.single_use:
            return True

        if self._consumed:
            await interaction.response.send_message(
                "この選択肢はすでに確定しています。",
                ephemeral=True,
            )
            return False

        self._consumed = True
        return True

    def _disable_children(self) -> None:
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True

    async def _safe_edit(self, **kwargs) -> None:
        if self.message is None:
            return

        try:
            await self.message.edit(view=self, **kwargs)
        except Exception:
            logger.exception("Failed to edit view message.")

    async def _safe_delete(self) -> None:
        if self.message is None:
            return

        try:
            await self.message.delete()
        except Exception:
            logger.exception("Failed to delete view message.")

    async def disable_and_stop(self, **kwargs) -> None:
        self._disable_children()
        if self.delete_on_use:
            await self._safe_delete()
        else:
            await self._safe_edit(**kwargs)
        self.stop()

    async def on_timeout(self) -> None:
        if self._consumed:
            return

        self._disable_children()
        if self.delete_on_timeout:
            await self._safe_delete()
        else:
            await self._safe_edit()
        self.stop()