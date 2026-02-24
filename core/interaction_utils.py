import asyncio
import os

import discord

DEFAULT_TIMEOUT_SEC = 180

def bot_add_prompt_text() -> str:
    invite_url = "https://discord.com/oauth2/authorize?client_id=1308611315878858762&permissions=2147601408&integration_type=0&scope=bot+applications.commands"
    if invite_url:
        return (
            "この機能は **Botとしてサーバーに追加** されていないと使えません。\n"
            f"追加リンク: {invite_url}"
        )
    return (
        "この機能は **Botとしてサーバーに追加** されていないと使えません。\n"
        "管理者にBot招待リンクの設定（`DISCORD_BOT_INVITE_URL`）を依頼してください。"
    )


def is_bot_member_in_guild(interaction: discord.Interaction) -> bool:
    guild = interaction.guild
    bot_user = interaction.client.user
    if guild is None or bot_user is None:
        return False
    return guild.get_member(bot_user.id) is not None


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
        self._consume_lock = asyncio.Lock()

    async def bind_to_response(self, interaction: discord.Interaction):
        try:
            self.message = await interaction.original_response()
        except Exception:
            self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.owner_id is None or interaction.user.id == self.owner_id:
            if not self.single_use:
                return True
            async with self._consume_lock:
                if self._consumed:
                    await interaction.response.send_message(
                        "この選択肢はすでに確定しています。",
                        ephemeral=True,
                    )
                    return False
                self._consumed = True
            self.stop()
            asyncio.create_task(self._cleanup_after_consume())
            return True
        await interaction.response.send_message(
            "このボタンはコマンド実行者のみ操作できます。",
            ephemeral=True,
        )
        return False

    def _disable_children(self):
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True

    async def _safe_edit(self, **kwargs):
        if self.message is None:
            return
        try:
            await self.message.edit(view=self, **kwargs)
        except Exception:
            pass

    async def _safe_delete(self):
        if self.message is None:
            return
        try:
            await self.message.delete()
        except Exception:
            pass

    async def _cleanup_after_consume(self):
        self._disable_children()
        if self.delete_on_use:
            await self._safe_delete()
            return
        await self._safe_edit()

    async def on_timeout(self):
        if self._consumed:
            return
        self._disable_children()
        if self.delete_on_timeout:
            await self._safe_delete()
        else:
            await self._safe_edit()
        self.stop()

    async def disable_and_stop(self, **kwargs):
        self._disable_children()
        await self._safe_edit(**kwargs)
        self.stop()
