from __future__ import annotations



import asyncio

import json

import logging

from dataclasses import dataclass

from typing import Any

from urllib.parse import quote



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

        # Allow joins while this match is loaded; bot re-enables idle lock after series end.

        "matchzy_kick_when_no_match_loaded": "0",

    }



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

        # wingman: true makes MatchZy set game_mode and reload the map — do not duplicate cvars.

        config["wingman"] = True



    return config





def serialize_match_config(match: ActiveMatch, settings: Settings) -> str:

    return json.dumps(build_matchzy_config(match, settings), indent=2)





class MatchZyService:

    def __init__(self, settings: Settings, console: ServerConsole) -> None:

        self.settings = settings

        self.console = console

    async def _run_console_sequence(
        self,
        commands: tuple[str, ...],
        *,
        delay_seconds: float = 0.75,
        failure_label: str = "MatchZy console command",
    ) -> list[str]:
        responses: list[str] = []
        for command in commands:
            try:
                responses.append(await self.console.execute(command))
            except Exception:
                logger.warning("%s failed: %s", failure_label, command)
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
        return responses



    def build_match_json_url(self, match_id: str) -> str:

        """Build the URL MatchZy fetches. Auth is in the query string (MatchZy header args are unreliable)."""

        key = quote(self.settings.matchzy_api_key, safe="")

        return f'{self.settings.public_url}/matches/{match_id}.json?key={key}'



    async def load_match_from_url(self, match_id: str) -> str:

        url = self.build_match_json_url(match_id)

        command = f'matchzy_loadmatch_url "{url}"'

        logger.info("Loading MatchZy match %s from %s", match_id, url.split("?", 1)[0])

        return await self.console.execute(command)



    async def reset_match_state(self) -> list[str]:

        """Clear any previous MatchZy match so loadmatch_url is not rejected."""

        responses: list[str] = []

        for command in ("css_endmatch", "css_forceend", "css_exitprac"):

            try:

                responses.append(await self.console.execute(command))

            except Exception:

                logger.debug("Could not send MatchZy reset command: %s", command)

        return responses



    async def enter_match_mode(self) -> str:

        return await self.console.execute("css_match")



    async def change_map(self, map_name: str) -> str:

        return await self.console.execute(f"css_map {map_name}")



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

    async def unlock_server_for_active_match(self) -> list[str]:
        """Allow joins while a match JSON is loaded."""
        return await self._run_console_sequence(
            ("matchzy_kick_when_no_match_loaded 0",),
            failure_label="MatchZy unlock command",
        )

    async def lock_server_when_idle(self) -> list[str]:
        """Kick everyone and block joins when no match is loaded."""
        # Enable matchModeOnly before endmatch so MatchZy's ResetMatch UpdatePlayersMap
        # sweep kicks connected players (teamPlayers are cleared on reset).
        return await self._run_console_sequence(
            (
                "matchzy_kick_when_no_match_loaded 1",
                "css_endmatch",
                "css_forceend",
                "css_exitprac",
            ),
            delay_seconds=1.0,
            failure_label="MatchZy idle-lock command",
        )



    async def deploy_match(self, match: ActiveMatch) -> list[str]:

        """Reset MatchZy, enter match mode, and load match JSON (triggers map change)."""

        responses: list[str] = []

        responses.extend(await self.unlock_server_for_active_match())

        responses.extend(await self.reset_match_state())

        await asyncio.sleep(2)

        responses.append(await self.enter_match_mode())

        await asyncio.sleep(1)

        responses.append(await self.load_match_from_url(match.match_id))

        return responses



    async def finalize_match_deploy(self, match: ActiveMatch) -> list[str]:

        """Run after the game port is back — do not send during map reload."""

        responses: list[str] = []

        responses.append(await self.set_ready_required(match.mode.total_players))

        responses.extend(await self.disable_early_forfeit())

        responses.extend(await self.unlock_server_for_active_match())

        return responses


