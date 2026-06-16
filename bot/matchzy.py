from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from config import MatchMode, Settings
from server_console import ServerConsole

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class QueuedPlayer:
    discord_id: int
    discord_name: str
    steam_id: str


@dataclass(frozen=True)
class ActiveMatch:
    match_id: str
    mode: MatchMode
    map_name: str
    team1: list[QueuedPlayer]
    team2: list[QueuedPlayer]
    team1_side: str = "ct"


def _matchzy_match_id(match_id: str) -> str | int:
    if match_id.isdigit():
        return int(match_id)
    return match_id


def _map_side_for_team1(team1_side: str) -> str:
    """Map Team Alpha (team1) starting side to MatchZy map_sides values."""
    normalized = team1_side.lower().strip()
    if normalized == "ct":
        return "team1_ct"
    if normalized == "t":
        return "team1_t"
    return "team1_ct"


def build_matchzy_config(match: ActiveMatch, settings: Settings) -> dict[str, Any]:
    team1_players = {player.steam_id: player.discord_name for player in match.team1}
    team2_players = {player.steam_id: player.discord_name for player in match.team2}

    cvars: dict[str, str] = {
        "hostname": f"MatchZy {match.mode.label} | {match.map_name}",
        # Do not auto-forfeit when players disconnect — play until a normal map result.
        "matchzy_ffw_enabled": "0",
        "matchzy_gg_enabled": "0",
    }

    if match.mode == MatchMode.TWO_V_TWO:
        cvars["game_mode"] = "2"
        cvars["game_type"] = "0"

    config: dict[str, Any] = {
        "matchid": _matchzy_match_id(match.match_id),
        "team1": {"name": "Team Alpha", "players": team1_players},
        "team2": {"name": "Team Bravo", "players": team2_players},
        "num_maps": 1,
        "maplist": [match.map_name],
        "map_sides": [_map_side_for_team1(match.team1_side)],
        "skip_veto": True,
        "clinch_series": True,
        "players_per_team": match.mode.players_per_team,
        "min_players_to_ready": match.mode.total_players,
        "cvars": cvars,
    }

    if match.mode == MatchMode.TWO_V_TWO:
        config["wingman"] = True

    return config


def serialize_match_config(match: ActiveMatch, settings: Settings) -> str:
    return json.dumps(build_matchzy_config(match, settings), indent=2)


class MatchZyService:
    def __init__(self, settings: Settings, console: ServerConsole) -> None:
        self.settings = settings
        self.console = console

    async def load_match_from_url(self, match_id: str) -> str:
        url = f'{self.settings.public_url}/matches/{match_id}.json'
        header_name = "X-API-Key"
        header_value = self.settings.matchzy_api_key
        command = (
            f'matchzy_loadmatch_url "{url}" "{header_name}" "{header_value}"'
        )
        return await self.console.execute(command)

    async def enter_match_mode(self) -> str:
        return await self.console.execute("css_match")

    async def force_start(self) -> str:
        return await self.console.execute("css_start")

    async def end_match(self) -> str:
        return await self.console.execute("css_endmatch")

    async def set_ready_required(self, count: int) -> str:
        return await self.console.execute(f"css_readyrequired {count}")

    async def disable_early_forfeit(self) -> list[str]:
        """Best-effort: keep matches running if a player disconnects (MatchZy Enhanced)."""
        responses: list[str] = []
        for command in ("matchzy_ffw_enabled 0", "matchzy_gg_enabled 0"):
            try:
                responses.append(await self.console.execute(command))
            except Exception:
                logger.debug("Could not send MatchZy command: %s", command)
        return responses

    async def deploy_match(self, match: ActiveMatch) -> list[str]:
        responses: list[str] = []
        responses.append(await self.enter_match_mode())
        responses.append(await self.load_match_from_url(match.match_id))
        responses.append(await self.set_ready_required(match.mode.total_players))
        responses.extend(await self.disable_early_forfeit())
        return responses
