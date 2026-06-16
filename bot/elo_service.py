"""ELO ratings, rosters, and leaderboards."""

import logging
from dataclasses import dataclass

from config import MatchMode, Settings
from elo import EloChange, calculate_elo_changes
from elo_season import (
    SEASON_META_KEY,
    EloSeason,
    build_season,
    format_season_start,
    parse_season_start,
    season_has_expired,
    utc_now,
)
from matchzy_events import extract_winner_team
from storage import Storage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MatchRoster:
    match_id: str
    mode: MatchMode
    team1_ids: list[int]
    team2_ids: list[int]
    team1_names: dict[int, str]
    team2_names: dict[int, str]


class EloService:
    def __init__(self, storage: Storage, settings: Settings) -> None:
        self.storage = storage
        self.default_elo = settings.default_elo
        self.k_factor = settings.k_factor
        self.leaderboard_limit = 10

    async def get_current_season(self) -> EloSeason:
        raw = await self.storage.get_bot_meta(SEASON_META_KEY)
        start = parse_season_start(raw)
        if start is None:
            start = utc_now()
            await self.storage.set_bot_meta(SEASON_META_KEY, format_season_start(start))
        return build_season(start)

    async def ensure_current_season(self) -> tuple[EloSeason, bool]:
        season = await self.get_current_season()
        if not season_has_expired(season):
            return season, False

        await self.storage.reset_all_player_elo()
        new_start = utc_now()
        await self.storage.set_bot_meta(SEASON_META_KEY, format_season_start(new_start))
        logger.info("ELO season reset — new season starts %s", new_start.isoformat())
        return build_season(new_start), True

    async def get_all_leaderboards(self) -> dict[MatchMode, list[dict]]:
        boards: dict[MatchMode, list[dict]] = {}
        for mode in MatchMode:
            boards[mode] = await self.get_leaderboard(mode, limit=self.leaderboard_limit)
        return boards

    async def resolve_match_id(self, raw_match_id: str) -> str | None:
        normalized = str(raw_match_id).strip()
        if not normalized or normalized == "unknown":
            active_ids = await self.storage.get_active_match_ids()
            if len(active_ids) == 1:
                return active_ids[0]
            return None

        if await self.storage.match_exists(normalized):
            return normalized

        active_ids = await self.storage.get_active_match_ids()
        if normalized in active_ids:
            return normalized

        if normalized.isdigit():
            for match_id in active_ids:
                if match_id == normalized or match_id.lstrip("0") == normalized.lstrip("0"):
                    return match_id

        if len(active_ids) == 1:
            return active_ids[0]

        return None

    async def get_roster(self, match_id: str) -> MatchRoster | None:
        record = await self.storage.get_match_record(match_id)
        if record is None:
            return None

        mode = MatchMode(record["mode"])
        roster = record.get("roster")
        if roster is None:
            return None

        return MatchRoster(
            match_id=match_id,
            mode=mode,
            team1_ids=roster["team1_ids"],
            team2_ids=roster["team2_ids"],
            team1_names=roster["team1_names"],
            team2_names=roster["team2_names"],
        )

    async def save_roster_from_match(self, match) -> None:
        roster = {
            "team1_ids": [player.discord_id for player in match.team1],
            "team2_ids": [player.discord_id for player in match.team2],
            "team1_names": {player.discord_id: player.discord_name for player in match.team1},
            "team2_names": {player.discord_id: player.discord_name for player in match.team2},
        }
        await self.storage.save_match_roster(match.match_id, roster)

    async def process_match_result(
        self,
        match_id: str,
        payload: dict,
    ) -> list[EloChange] | None:
        if await self.storage.is_elo_processed(match_id):
            return None

        await self.ensure_current_season()

        winner_team = extract_winner_team(payload)
        if winner_team is None:
            logger.info("No winner for match %s; skipping ELO update", match_id)
            return None

        roster = await self.get_roster(match_id)
        if roster is None:
            logger.warning("No roster found for match %s; skipping ELO update", match_id)
            return None

        team1_ratings = await self.storage.get_player_ratings(
            roster.team1_ids,
            roster.mode.value,
            self.default_elo,
        )
        team2_ratings = await self.storage.get_player_ratings(
            roster.team2_ids,
            roster.mode.value,
            self.default_elo,
        )

        changes = calculate_elo_changes(
            roster.team1_ids,
            roster.team2_ids,
            roster.team1_names,
            roster.team2_names,
            team1_ratings,
            team2_ratings,
            winner_team,
            self.k_factor,
            self.default_elo,
        )

        await self.storage.apply_elo_changes(roster.mode.value, changes)
        await self.storage.mark_elo_processed(match_id)
        return changes

    async def get_profile_elo(self, discord_id: int) -> dict[MatchMode, dict[str, int]]:
        raw = await self.storage.get_all_player_elo(discord_id)
        profile: dict[MatchMode, dict[str, int]] = {}
        for mode in MatchMode:
            stats = raw.get(mode.value, {"rating": self.default_elo, "wins": 0, "losses": 0})
            profile[mode] = stats
        return profile

    async def get_leaderboard(self, mode: MatchMode, limit: int = 10) -> list[dict]:
        return await self.storage.get_leaderboard(mode.value, limit, self.default_elo)
