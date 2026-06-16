from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from config import MatchMode

if TYPE_CHECKING:
    from live_match import LiveMatchSnapshot
FINISH_EVENTS = frozenset({"series_end", "series_result", "match_end"})
LIVE_UPDATE_EVENTS = frozenset(
    {
        "series_start",
        "going_live",
        "round_end",
        "map_result",
        "series_end",
        "series_result",
        "match_end",
    }
)

_MATCH_ID_KEYS = ("matchid", "match_id", "matchId")
_NESTED_MATCH_ID_KEYS = ("id", "matchid", "match_id", "matchId")


class WebhookPayloadError(ValueError):
    """Raised when a MatchZy webhook body cannot be parsed."""


def parse_webhook_json(raw: bytes | str) -> dict[str, Any]:
    """Parse a MatchZy POST body regardless of Content-Type header."""
    if isinstance(raw, bytes):
        text = raw.decode("utf-8-sig").strip()
    else:
        text = raw.strip()

    if not text:
        raise WebhookPayloadError("Empty webhook body")

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WebhookPayloadError("Invalid JSON payload") from exc

    if not isinstance(parsed, dict):
        raise WebhookPayloadError("Webhook payload must be a JSON object")

    return parsed


def normalize_event_name(event_name: str) -> str:
    normalized = event_name.strip().lower()
    if normalized == "series_result":
        return "series_end"
    return normalized


def extract_match_id(payload: dict[str, Any]) -> str:
    for key in _MATCH_ID_KEYS:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()

    match_block = payload.get("match")
    if isinstance(match_block, dict):
        for key in _NESTED_MATCH_ID_KEYS:
            value = match_block.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()

    return "unknown"


def parse_event_payload(payload: dict[str, Any]) -> tuple[str, str]:
    """Return normalized (event_name, match_id) from a webhook payload."""
    raw_event = str(payload.get("event", "unknown"))
    return normalize_event_name(raw_event), extract_match_id(payload)


def is_finish_event(event_name: str) -> bool:
    return normalize_event_name(event_name) in FINISH_EVENTS


def is_live_update_event(event_name: str) -> bool:
    return normalize_event_name(event_name) in LIVE_UPDATE_EVENTS


def extract_series_scores(payload: dict[str, Any]) -> tuple[int | None, int | None]:
    """Read map/series scores from MatchZy series_end or map_result payloads."""
    team1_score = payload.get("team1_series_score")
    team2_score = payload.get("team2_series_score")

    team1 = payload.get("team1")
    team2 = payload.get("team2")
    if team1_score is None and isinstance(team1, dict):
        team1_score = team1.get("series_score", team1.get("score"))
    if team2_score is None and isinstance(team2, dict):
        team2_score = team2.get("series_score", team2.get("score"))

    try:
        if team1_score is not None and team2_score is not None:
            return int(team1_score), int(team2_score)
    except (TypeError, ValueError):
        pass
    return None, None


def extract_winner_team(payload: dict[str, Any]) -> str | None:
    """Return team1, team2, or None from a MatchZy finish webhook payload."""
    winner = payload.get("winner")
    if isinstance(winner, dict):
        team = winner.get("team")
        if isinstance(team, str):
            normalized = team.strip().lower()
            if normalized in {"team1", "1", "alpha"}:
                return "team1"
            if normalized in {"team2", "2", "bravo"}:
                return "team2"
            if normalized in {"none", "draw", ""}:
                return None

    team1_score, team2_score = extract_series_scores(payload)
    if team1_score is None or team2_score is None:
        return None
    if team1_score == team2_score:
        return None
    return "team1" if team1_score > team2_score else "team2"


def winner_from_round_scores(
    team1_rounds: int | None,
    team2_rounds: int | None,
) -> str | None:
    if team1_rounds is None or team2_rounds is None or team1_rounds == team2_rounds:
        return None
    return "team1" if team1_rounds > team2_rounds else "team2"


def build_series_end_payload(
    match_id: str,
    *,
    winner_team: str | None = None,
    team1_series_score: int | None = None,
    team2_series_score: int | None = None,
    source: str = "synthetic",
) -> dict[str, Any]:
    """Build a series_end-shaped payload for non-webhook finish paths."""
    payload: dict[str, Any] = {
        "event": "series_end",
        "matchid": match_id,
        "source": source,
    }
    if winner_team in {"team1", "team2"}:
        payload["winner"] = {"team": winner_team}
    if team1_series_score is not None and team2_series_score is not None:
        payload["team1_series_score"] = team1_series_score
        payload["team2_series_score"] = team2_series_score
    return payload


def build_series_end_payload_from_snapshot(
    match_id: str,
    winner_team: str | None,
    snapshot: LiveMatchSnapshot | None,
    *,
    mode: MatchMode = MatchMode.FIVE_V_FIVE,
    source: str = "synthetic",
) -> dict[str, Any]:
    team1_series: int | None = None
    team2_series: int | None = None
    resolved_winner = winner_team

    if snapshot is not None:
        if snapshot.team1_series_score is not None and snapshot.team2_series_score is not None:
            team1_series = snapshot.team1_series_score
            team2_series = snapshot.team2_series_score
        elif snapshot_has_completed_map(snapshot, mode):
            t1 = snapshot.team1_round_score
            t2 = snapshot.team2_round_score
            if t1 is not None and t2 is not None:
                team1_series = 1 if t1 > t2 else 0
                team2_series = 1 if t2 > t1 else 0
                if resolved_winner is None:
                    resolved_winner = winner_from_round_scores(t1, t2)

    return build_series_end_payload(
        match_id,
        winner_team=resolved_winner,
        team1_series_score=team1_series,
        team2_series_score=team2_series,
        source=source,
    )


def finish_payload_summary(payload: dict[str, Any]) -> str:
    winner = extract_winner_team(payload)
    team1_score, team2_score = extract_series_scores(payload)
    parts = [f"winner={winner or 'none'}"]
    if team1_score is not None and team2_score is not None:
        parts.append(f"score={team1_score}-{team2_score}")
    round1, round2 = extract_round_scores(payload)
    if round1 is not None and round2 is not None:
        parts.append(f"rounds={round1}-{round2}")
    return ", ".join(parts)


def extract_round_scores(payload: dict[str, Any]) -> tuple[int | None, int | None]:
    """Read in-map round scores from round_end or map_result payloads."""
    team1 = payload.get("team1")
    team2 = payload.get("team2")
    team1_score = team1.get("score") if isinstance(team1, dict) else None
    team2_score = team2.get("score") if isinstance(team2, dict) else None
    try:
        if team1_score is not None and team2_score is not None:
            return int(team1_score), int(team2_score)
    except (TypeError, ValueError):
        pass
    return None, None


def rounds_to_win(mode: MatchMode) -> int:
    return 9 if mode == MatchMode.TWO_V_TWO else 13


def is_completed_map_score(
    team1_rounds: int,
    team2_rounds: int,
    mode: MatchMode,
) -> bool:
    """True when round scores reflect a finished map (not a mid-match disconnect)."""
    if team1_rounds == team2_rounds:
        return False
    threshold = rounds_to_win(mode)
    return max(team1_rounds, team2_rounds) >= threshold


def is_forfeit_payload(payload: dict[str, Any]) -> bool:
    for key in ("forfeit", "walkover", "ffw", "is_forfeit", "cancelled"):
        value = payload.get(key)
        if value in (True, 1, "1", "true", "True"):
            return True

    for key in ("reason", "end_reason", "result", "message"):
        text = str(payload.get(key, "")).lower()
        if any(token in text for token in ("forfeit", "walkover", "ffw", "abandon", "disconnect")):
            return True

    winner = payload.get("winner")
    if isinstance(winner, dict):
        side = str(winner.get("side", "")).lower()
        if side in {"none", "forfeit"}:
            return True

    return False


def is_decisive_series_score(
    team1_score: int,
    team2_score: int,
    *,
    num_maps: int = 1,
) -> bool:
    if team1_score == team2_score:
        return False
    maps_to_win = max(1, (num_maps // 2) + 1)
    return max(team1_score, team2_score) >= maps_to_win


def should_finish_match(
    payload: dict[str, Any],
    event_name: str,
    mode: MatchMode,
    *,
    num_maps: int = 1,
) -> tuple[bool, str]:
    """
    Return whether the bot should end the match from this payload.
    Blocks forfeit/disconnect ends and mid-map scores.
    """
    if is_forfeit_payload(payload):
        return False, "forfeit or disconnect-style payload"

    event_name = normalize_event_name(event_name)

    if event_name == "series_end":
        if extract_winner_team(payload) is None:
            return False, "series_end without a winner"

        team1_series, team2_series = extract_series_scores(payload)
        if team1_series is not None and team2_series is not None:
            if is_decisive_series_score(team1_series, team2_series, num_maps=num_maps):
                return True, f"decisive series score {team1_series}-{team2_series}"

        team1_rounds, team2_rounds = extract_round_scores(payload)
        if team1_rounds is not None and team2_rounds is not None:
            if is_completed_map_score(team1_rounds, team2_rounds, mode):
                return True, f"completed map score {team1_rounds}-{team2_rounds}"

        if payload.get("time_until_restore") is not None and team1_series is not None:
            return True, "official MatchZy series_end"

        return False, "series_end without a completed result"

    if event_name == "map_result":
        team1_rounds, team2_rounds = extract_round_scores(payload)
        if team1_rounds is None or team2_rounds is None:
            return False, "map_result missing round scores"
        if is_completed_map_score(team1_rounds, team2_rounds, mode):
            return True, f"completed map score {team1_rounds}-{team2_rounds}"
        return False, f"map_result score {team1_rounds}-{team2_rounds} is not a final map"

    if event_name in FINISH_EVENTS:
        team1_rounds, team2_rounds = extract_round_scores(payload)
        if team1_rounds is not None and team2_rounds is not None:
            if is_completed_map_score(team1_rounds, team2_rounds, mode):
                return True, f"completed map score {team1_rounds}-{team2_rounds}"
        return False, f"{event_name} without completed map scores"

    return False, f"event {event_name} is not a finish signal"


def snapshot_has_completed_map(snapshot: LiveMatchSnapshot | None, mode: MatchMode) -> bool:
    if snapshot is None:
        return False
    if normalize_event_name(getattr(snapshot, "last_event", "")) == "series_end":
        return True
    team1 = getattr(snapshot, "team1_round_score", None)
    team2 = getattr(snapshot, "team2_round_score", None)
    if team1 is None or team2 is None:
        return False
    return is_completed_map_score(team1, team2, mode)

