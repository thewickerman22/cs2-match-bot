from __future__ import annotations

from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from bot_app import MatchBot


def votes_required_for_roster(roster_size: int) -> int:
    if roster_size <= 2:
        return 1
    return roster_size // 2 + 1


class MatchResultReportView(discord.ui.View):
    """Roster-only controls on the live #match-results embed when webhooks fail."""

    def __init__(self, bot: MatchBot, match_id: str) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.match_id = match_id

        alpha = discord.ui.Button(
            label="Report: Team Alpha Won",
            style=discord.ButtonStyle.success,
            custom_id=f"cs2match:report:{match_id}:team1",
        )
        alpha.callback = self._report_alpha
        self.add_item(alpha)

        bravo = discord.ui.Button(
            label="Report: Team Bravo Won",
            style=discord.ButtonStyle.danger,
            custom_id=f"cs2match:report:{match_id}:team2",
        )
        bravo.callback = self._report_bravo
        self.add_item(bravo)

        end_match = discord.ui.Button(
            label="End Match (No ELO)",
            style=discord.ButtonStyle.secondary,
            custom_id=f"cs2match:playerend:{match_id}",
        )
        end_match.callback = self._player_end_match
        self.add_item(end_match)

    async def _report_alpha(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_match_result_report(interaction, self.match_id, "team1")

    async def _report_bravo(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_match_result_report(interaction, self.match_id, "team2")

    async def _player_end_match(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_match_player_end_request(interaction, self.match_id)
