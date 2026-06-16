from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from config import MatchMode
from guild_setup import (
    COMMANDS_CHANNEL_NAME,
    ELO_CHANNEL_NAME,
    RESULTS_CHANNEL_NAME,
    STATUS_CHANNEL_NAME,
)

if TYPE_CHECKING:
    from bot_app import MatchBot

# Admin panel reactions — react on the pinned admin message to run an action.
REACTION_END_MATCH = "🛑"
REACTION_FORCE_START = "▶️"
REACTION_TEST_SERVER = "🔌"
REACTION_RESET_1V1 = "1️⃣"
REACTION_RESET_2V2 = "2️⃣"
REACTION_RESET_5V5 = "5️⃣"

ADMIN_PANEL_REACTIONS: dict[str, str] = {
    REACTION_END_MATCH: "endmatch",
    REACTION_FORCE_START: "forcestart",
    REACTION_TEST_SERVER: "testserver",
    REACTION_RESET_1V1: "resetcaptains_1v1",
    REACTION_RESET_2V2: "resetcaptains_2v2",
    REACTION_RESET_5V5: "resetcaptains_5v5",
}


def build_player_commands_embed() -> discord.Embed:
    return discord.Embed(
        title="How to play",
        description=(
            f"Join a **Queue » …** voice channel to enter the queue. "
            f"Use **#{STATUS_CHANNEL_NAME}** for ready reactions and lobby buttons.\n\n"
            f"Use the buttons below or on **#{STATUS_CHANNEL_NAME}** — no typing required."
        ),
        color=discord.Color.blurple(),
    ).add_field(
        name="Player controls",
        value=(
            "**My Profile** — your linked Steam account and ELO\n"
            "**Leaderboard** — top players by mode (dropdown)\n"
            "**Link Steam Account** / **Unlink Steam** — manage your Steam link\n\n"
            f"**#{STATUS_CHANNEL_NAME}** buttons: Ban Map, Pick Side, Vote Captains, Pick Player\n"
            f"**#{STATUS_CHANNEL_NAME}** reactions: ✅ ready · ❌ unready\n"
            f"**#{RESULTS_CHANNEL_NAME}** — report winner or **End Match (No ELO)** if stuck\n"
            f"**#{ELO_CHANNEL_NAME}** — live leaderboard (updates after each match)"
        ),
        inline=False,
    )


def build_admin_commands_embed() -> discord.Embed:
    reaction_lines = [
        f"{REACTION_END_MATCH} — **End active match** (no ELO, cleanup voice)",
        f"{REACTION_FORCE_START} — **Force-start** MatchZy on the server",
        f"{REACTION_TEST_SERVER} — **Test server** connection (RCON / DatHost)",
        f"{REACTION_RESET_1V1} — **Reset lobby** for 1v1",
        f"{REACTION_RESET_2V2} — **Reset lobby** for 2v2",
        f"{REACTION_RESET_5V5} — **Reset lobby** for 5v5",
    ]
    return discord.Embed(
        title="Admin controls",
        description=(
            f"Use the **Refresh Setup** button below or react on this message "
            f"(admin role or server administrator only).\n\n"
            f"Channel: **#{COMMANDS_CHANNEL_NAME}**"
        ),
        color=discord.Color.red(),
    ).add_field(
        name="Button",
        value="**Refresh Setup** — create or refresh all matchmaking channels and panels",
        inline=False,
    ).add_field(
        name="Quick actions (react below)",
        value="\n".join(reaction_lines),
        inline=False,
    )


class LeaderboardModeSelect(discord.ui.Select):
    def __init__(self, bot: MatchBot) -> None:
        super().__init__(
            placeholder="View leaderboard…",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="1v1 leaderboard", value=MatchMode.ONE_V_ONE.value),
                discord.SelectOption(label="2v2 leaderboard", value=MatchMode.TWO_V_TWO.value),
                discord.SelectOption(label="5v5 leaderboard", value=MatchMode.FIVE_V_FIVE.value),
            ],
            custom_id="cs2match:panel:leaderboard",
        )
        self.bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        from bot_app import parse_match_mode

        mode = parse_match_mode(self.values[0])
        await self.bot.handle_leaderboard_request(interaction, mode)


class PlayerCommandPanelView(discord.ui.View):
    def __init__(self, bot: MatchBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.add_item(LeaderboardModeSelect(bot))

    @discord.ui.button(
        label="My Profile",
        style=discord.ButtonStyle.primary,
        custom_id="cs2match:panel:profile",
        row=1,
    )
    async def profile_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.bot.handle_show_profile(interaction)

    @discord.ui.button(
        label="Link Steam Account",
        style=discord.ButtonStyle.secondary,
        custom_id="cs2match:panel:steamlink",
        row=1,
    )
    async def link_steam_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.bot.handle_steam_link_request(interaction)

    @discord.ui.button(
        label="Unlink Steam",
        style=discord.ButtonStyle.danger,
        custom_id="cs2match:panel:steamunlink",
        row=1,
    )
    async def unlink_steam_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.bot.handle_steam_unlink_request(interaction)


class AdminCommandPanelView(discord.ui.View):
    def __init__(self, bot: MatchBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Refresh Setup",
        style=discord.ButtonStyle.success,
        custom_id="cs2match:panel:admin:setup",
    )
    async def refresh_setup_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.bot.handle_admin_setup_request(interaction)
