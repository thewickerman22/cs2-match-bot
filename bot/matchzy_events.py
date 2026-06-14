from __future__ import annotations

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


def extract_match_id(payload: dict) -> str:
    for key in ("matchid", "match_id", "matchId"):
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return "unknown"


def normalize_event_name(event_name: str) -> str:
    if event_name == "series_result":
        return "series_end"
    return event_name
