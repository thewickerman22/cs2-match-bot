from __future__ import annotations

from dataclasses import dataclass

from config import MatchMode


@dataclass(frozen=True)
class EloChange:
    discord_id: int
    discord_name: str
    old_rating: int
    new_rating: int
    delta: int
    won: bool


def parse_winner_team(payload: dict) -> str | None:
    winner = payload.get("winner")
    if isinstance(winner, dict):
        team = winner.get("team")
        if team in {"team1", "team2"}:
            return team
        if team in {"none", "draw", ""}:
            return None

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
        return None
    if team1_score == team2_score:
        return None
    return "team1" if team1_score > team2_score else "team2"


def expected_score(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400))


def calculate_team_deltas(
    team_ratings: list[int],
    opponent_ratings: list[int],
    team_won: bool,
    k_factor: int,
) -> list[int]:
    if not team_ratings or not opponent_ratings:
        return [0 for _ in team_ratings]

    team_avg = sum(team_ratings) / len(team_ratings)
    opponent_avg = sum(opponent_ratings) / len(opponent_ratings)
    expected = expected_score(team_avg, opponent_avg)
    actual = 1.0 if team_won else 0.0
    delta = round(k_factor * (actual - expected))
    if team_won:
        delta = max(1, delta)
    else:
        delta = min(-1, delta)
    return [delta for _ in team_ratings]


def calculate_elo_changes(
    team1_ids: list[int],
    team2_ids: list[int],
    team1_names: dict[int, str],
    team2_names: dict[int, str],
    team1_ratings: dict[int, int],
    team2_ratings: dict[int, int],
    winner_team: str,
    k_factor: int,
    default_elo: int,
) -> list[EloChange]:
    team1_won = winner_team == "team1"

    t1_values = [team1_ratings.get(player_id, default_elo) for player_id in team1_ids]
    t2_values = [team2_ratings.get(player_id, default_elo) for player_id in team2_ids]

    t1_deltas = calculate_team_deltas(t1_values, t2_values, team1_won, k_factor)
    t2_deltas = calculate_team_deltas(t2_values, t1_values, not team1_won, k_factor)

    changes: list[EloChange] = []
    for player_id, old_rating, delta in zip(team1_ids, t1_values, t1_deltas, strict=True):
        changes.append(
            EloChange(
                discord_id=player_id,
                discord_name=team1_names.get(player_id, str(player_id)),
                old_rating=old_rating,
                new_rating=old_rating + delta,
                delta=delta,
                won=team1_won,
            )
        )

    for player_id, old_rating, delta in zip(team2_ids, t2_values, t2_deltas, strict=True):
        changes.append(
            EloChange(
                discord_id=player_id,
                discord_name=team2_names.get(player_id, str(player_id)),
                old_rating=old_rating,
                new_rating=old_rating + delta,
                delta=delta,
                won=not team1_won,
            )
        )

    return changes


def format_elo_summary(mode: MatchMode, changes: list[EloChange]) -> str:
    lines = [f"**ELO updated — {mode.label}**"]
    for change in sorted(changes, key=lambda item: item.delta, reverse=True):
        sign = "+" if change.delta >= 0 else ""
        result = "WIN" if change.won else "LOSS"
        lines.append(
            f"- {change.discord_name}: `{change.old_rating}` → `{change.new_rating}` "
            f"({sign}{change.delta}) [{result}]"
        )
    return "\n".join(lines)
