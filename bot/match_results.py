from __future__ import annotations

import discord

from config import MatchMode
from elo import EloChange, parse_winner_team


def parse_match_scores(payload: dict) -> tuple[int | None, int | None]:
    team1_score = payload.get("team1_series_score")
    team2_score = payload.get("team2_series_score")

    if team1_score is None or team2_score is None:
        team1 = payload.get("team1")
        team2 = payload.get("team2")
        if isinstance(team1, dict):
            team1_score = team1.get("series_score", team1.get("score"))
        if isinstance(team2, dict):
            team2_score = team2.get("series_score", team2.get("score"))

    if team1_score is None or team2_score is None:
        return None, None
    return int(team1_score), int(team2_score)


def _format_team_lines(
    player_ids: list[int],
    player_names: dict[int, str],
    elo_changes: list[EloChange] | None,
) -> str:
    change_map = {change.discord_id: change for change in (elo_changes or [])}
    lines: list[str] = []
    for player_id in player_ids:
        name = player_names.get(player_id, str(player_id))
        change = change_map.get(player_id)
        if change is None:
            lines.append(f"- <@{player_id}> ({name})")
            continue
        sign = "+" if change.delta >= 0 else ""
        lines.append(
            f"- <@{player_id}> ({name}) — `{change.old_rating}` → `{change.new_rating}` "
            f"({sign}{change.delta})"
        )
    return "\n".join(lines) if lines else "_No players_"


def build_match_result_embed(
    match_id: str,
    mode: MatchMode,
    map_name: str,
    team1_ids: list[int],
    team2_ids: list[int],
    team1_names: dict[int, str],
    team2_names: dict[int, str],
    payload: dict,
    elo_changes: list[EloChange] | None = None,
) -> discord.Embed:
    winner_team = parse_winner_team(payload)
    team1_score, team2_score = parse_match_scores(payload)

    if winner_team == "team1":
        title = "Team Alpha wins"
        color = discord.Color.gold()
    elif winner_team == "team2":
        title = "Team Bravo wins"
        color = discord.Color.gold()
    else:
        title = "Match ended"
        color = discord.Color.light_grey()

    embed = discord.Embed(
        title=title,
        description=f"**{mode.label}** on `{map_name}`",
        color=color,
    )
    embed.set_footer(text=f"Match ID: {match_id}")

    if team1_score is not None and team2_score is not None:
        embed.add_field(
            name="Final score",
            value=f"Team Alpha **{team1_score}** — **{team2_score}** Team Bravo",
            inline=False,
        )

    embed.add_field(
        name="Team Alpha",
        value=_format_team_lines(team1_ids, team1_names, elo_changes),
        inline=True,
    )
    embed.add_field(
        name="Team Bravo",
        value=_format_team_lines(team2_ids, team2_names, elo_changes),
        inline=True,
    )

    if elo_changes:
        embed.add_field(
            name="ELO",
            value="Rating changes shown next to each player.",
            inline=False,
        )
    elif winner_team is None:
        embed.add_field(
            name="ELO",
            value="No rating change (draw or cancelled).",
            inline=False,
        )

    embed.timestamp = discord.utils.utcnow()
    return embed
