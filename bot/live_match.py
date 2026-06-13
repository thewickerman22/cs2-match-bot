from __future__ import annotations

from dataclasses import dataclass, field

import discord

from config import MatchMode


@dataclass
class LiveMatchSnapshot:
    status: str = "Waiting for players"
    team1_round_score: int | None = None
    team2_round_score: int | None = None
    team1_series_score: int | None = None
    team2_series_score: int | None = None
    round_number: int | None = None
    map_number: int | None = None
    last_event: str = ""

    def merge_event(self, event_name: str, payload: dict) -> None:
        self.last_event = event_name
        if "map_number" in payload:
            self.map_number = int(payload["map_number"])
        if "round_number" in payload:
            self.round_number = int(payload["round_number"])

        team1 = payload.get("team1")
        team2 = payload.get("team2")
        if isinstance(team1, dict):
            if team1.get("score") is not None:
                self.team1_round_score = int(team1["score"])
            if team1.get("series_score") is not None:
                self.team1_series_score = int(team1["series_score"])
        if isinstance(team2, dict):
            if team2.get("score") is not None:
                self.team2_round_score = int(team2["score"])
            if team2.get("series_score") is not None:
                self.team2_series_score = int(team2["series_score"])

        if payload.get("team1_series_score") is not None:
            self.team1_series_score = int(payload["team1_series_score"])
        if payload.get("team2_series_score") is not None:
            self.team2_series_score = int(payload["team2_series_score"])

        if event_name == "series_start":
            self.status = "Series started — waiting for go live"
        elif event_name == "going_live":
            self.status = "Live"
        elif event_name == "round_end":
            self.status = "Live"
        elif event_name in {"map_result", "series_end", "match_end"}:
            self.status = "Finished"


LIVE_UPDATE_EVENTS = frozenset(
    {"series_start", "going_live", "round_end", "map_result", "series_end", "match_end"}
)


def _format_team_lines(
    player_ids: list[int],
    player_names: dict[int, str],
) -> str:
    lines: list[str] = []
    for player_id in player_ids:
        name = player_names.get(player_id, str(player_id))
        lines.append(f"- <@{player_id}> ({name})")
    return "\n".join(lines) if lines else "_No players_"


def _score_line(snapshot: LiveMatchSnapshot) -> str:
    if snapshot.team1_round_score is None or snapshot.team2_round_score is None:
        return "_Score pending_"
    return f"Team Alpha **{snapshot.team1_round_score}** — **{snapshot.team2_round_score}** Team Bravo"


def _series_line(snapshot: LiveMatchSnapshot) -> str | None:
    if snapshot.team1_series_score is None or snapshot.team2_series_score is None:
        return None
    if snapshot.team1_series_score == 0 and snapshot.team2_series_score == 0:
        return None
    return (
        f"Series: Team Alpha **{snapshot.team1_series_score}** — "
        f"**{snapshot.team2_series_score}** Team Bravo"
    )


def build_live_match_embed(
    match_id: str,
    mode: MatchMode,
    map_name: str,
    team1_ids: list[int],
    team2_ids: list[int],
    team1_names: dict[int, str],
    team2_names: dict[int, str],
    snapshot: LiveMatchSnapshot,
    *,
    server_connect_field: str | None = None,
) -> discord.Embed:
    status_label = snapshot.status
    if status_label == "Live":
        title = f"🔴 LIVE — {mode.label}"
        color = discord.Color.red()
    elif status_label == "Finished":
        title = f"Match finished — {mode.label}"
        color = discord.Color.gold()
    else:
        title = f"Match starting — {mode.label}"
        color = discord.Color.orange()

    embed = discord.Embed(
        title=title,
        description=f"**{status_label}** · `{map_name}`",
        color=color,
    )
    embed.add_field(name="Score", value=_score_line(snapshot), inline=False)

    series_line = _series_line(snapshot)
    if series_line:
        embed.add_field(name="Series", value=series_line, inline=False)

    meta_bits = []
    if snapshot.round_number is not None:
        meta_bits.append(f"Round **{snapshot.round_number}**")
    if snapshot.map_number is not None:
        meta_bits.append(f"Map **{snapshot.map_number + 1}**")
    if meta_bits:
        embed.add_field(name="Progress", value=" · ".join(meta_bits), inline=False)

    embed.add_field(
        name="Team Alpha",
        value=_format_team_lines(team1_ids, team1_names),
        inline=True,
    )
    embed.add_field(
        name="Team Bravo",
        value=_format_team_lines(team2_ids, team2_names),
        inline=True,
    )

    if server_connect_field:
        embed.add_field(name="Join server", value=server_connect_field, inline=False)

    embed.set_footer(text=f"Match ID: {match_id} · Updates automatically")
    embed.timestamp = discord.utils.utcnow()
    return embed
