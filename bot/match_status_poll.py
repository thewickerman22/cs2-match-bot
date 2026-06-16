from __future__ import annotations

import json
import logging
from typing import Any

from config import ServerProvider, Settings
from server_console import RconConsole, ServerConsole

logger = logging.getLogger(__name__)

_FINISHED_GAMESTATES = frozenset({"none", "postgame", "post_game", "idle"})


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if not text:
        return None

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None

    try:
        parsed = json.loads(text[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def parse_get5_status(raw: str) -> tuple[str | None, str | None]:
    """Return (gamestate, match_id) from a get5_status console response."""
    payload = _extract_json_object(raw)
    if payload is None:
        return None, None

    gamestate = payload.get("gamestate")
    if gamestate is not None:
        gamestate = str(gamestate).strip().lower()
    else:
        gamestate = None

    match_id: str | None = None
    match_block = payload.get("match")
    if isinstance(match_block, dict):
        for key in ("id", "matchid", "match_id"):
            value = match_block.get(key)
            if value is not None and str(value).strip():
                match_id = str(value).strip()
                break

    if match_id is None:
        for key in ("matchid", "match_id", "matchId"):
            value = payload.get(key)
            if value is not None and str(value).strip():
                match_id = str(value).strip()
                break

    return gamestate, match_id


def is_finished_gamestate(gamestate: str | None) -> bool:
    if gamestate is None:
        return False
    normalized = gamestate.strip().lower()
    if normalized in _FINISHED_GAMESTATES:
        return True
    return normalized.endswith("postgame") or normalized.endswith("post_game")


async def fetch_match_server_status(
    console: ServerConsole,
    settings: Settings,
) -> tuple[str | None, str | None] | None:
    """Poll MatchZy via RCON get5_status. Returns None when polling is unavailable."""
    if settings.server_provider != ServerProvider.LOCAL:
        return None
    if not isinstance(console, RconConsole):
        return None

    try:
        raw = await console.execute("get5_status")
    except Exception:
        logger.exception("get5_status poll failed")
        return None

    gamestate, match_id = parse_get5_status(raw)
    if gamestate is None and match_id is None:
        logger.debug("get5_status returned unparsable output: %s", raw[:200])
        return None

    return gamestate, match_id


__all__ = [
    "fetch_match_server_status",
    "is_finished_gamestate",
    "parse_get5_status",
]
