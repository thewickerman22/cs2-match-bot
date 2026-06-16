from __future__ import annotations

from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from bot_app import MatchBot


class SteamLinkModal(discord.ui.Modal, title="Link Steam Account"):
    steam64 = discord.ui.TextInput(
        label="Steam64 ID",
        placeholder="76561198012345678",
        min_length=1,
        max_length=120,
        required=True,
    )

    def __init__(self, bot: MatchBot) -> None:
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        success, message = await self.bot.link_steam_account(
            interaction.user.id,
            interaction.user.display_name,
            self.steam64.value,
        )
        if success:
            message = (
                f"{message}\n"
                "You can now join a **Queue » …** voice channel and react ✅ when ready."
            )
        await interaction.response.send_message(
            message,
            ephemeral=interaction.guild is not None,
        )


class SteamUnlinkConfirmView(discord.ui.View):
    def __init__(self, bot: MatchBot) -> None:
        super().__init__(timeout=60)
        self.bot = bot

    @discord.ui.button(
        label="Yes, unlink",
        style=discord.ButtonStyle.danger,
    )
    async def confirm_unlink(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        success, message = await self.bot.unlink_steam_account(interaction.user.id)
        await interaction.response.edit_message(content=message, view=None)
        if success and interaction.guild is not None:
            await self.bot.refresh_queue_status(interaction.guild)

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.secondary,
    )
    async def cancel_unlink(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.edit_message(
            content="Steam unlink cancelled.",
            view=None,
        )


class SteamLinkDmView(discord.ui.View):
    def __init__(self, bot: MatchBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Link Steam Account",
        style=discord.ButtonStyle.primary,
        custom_id="cs2match:steamlink_dm",
    )
    async def link_steam_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        profile = await self.bot.storage.get_player(interaction.user.id)
        if profile is not None:
            steam_id, _ = profile
            await interaction.response.send_message(
                f"Your Steam ID `{steam_id}` is already linked. "
                "Click **Unlink Steam** below to remove it.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(SteamLinkModal(self.bot))

    @discord.ui.button(
        label="Unlink Steam",
        style=discord.ButtonStyle.danger,
        custom_id="cs2match:steamunlink_dm",
    )
    async def unlink_steam_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.bot.handle_steam_unlink_request(interaction)
