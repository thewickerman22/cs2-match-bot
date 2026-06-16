from __future__ import annotations

import discord

from config import MatchMode
from elo_season import EloSeason


def _format_mode_leaderboard(rows: list[dict], *, empty_text: str) -> str:
    if not rows:
        return empty_text

    medals = ("🥇", "🥈", "🥉")
    lines: list[str] = []
    for index, row in enumerate(rows, start=1):
        prefix = medals[index - 1] if index <= len(medals) else f"{index}."
        lines.append(
            f"{prefix} <@{row['discord_id']}> — **{row['rating']}** "
            f"({row['wins']}W / {row['losses']}L)"
        )
    return "\n".join(lines)


def build_leaderboard_embed(
    season: EloSeason,
    boards: dict[MatchMode, list[dict]],
    *,
    default_elo: int,
) -> discord.Embed:
    embed = discord.Embed(
        title="CS2 ELO Leaderboard",
        description=(
            f"**Season:** {season.label}\n"
            f"Ratings reset every **3 months** (next reset "
            f"<t:{int(season.end.timestamp())}:R>).\n"
            f"Win a match for **positive** ELO, lose for **negative** ELO. "
            f"Starting rating: `{default_elo}`."
        ),
        color=discord.Color.gold(),
    )

    for mode in MatchMode:
        embed.add_field(
            name=mode.label,
            value=_format_mode_leaderboard(
                boards.get(mode, []),
                empty_text="_No ranked matches yet this season._",
            ),
            inline=False,
        )

    embed.set_footer(text="Updated automatically after each ranked match.")
    embed.timestamp = discord.utils.utcnow()
    return embed
