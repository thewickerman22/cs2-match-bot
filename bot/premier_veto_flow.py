from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from captain_flow import CaptainTeam
from config import MatchMode


class PremierVetoPhase(str, Enum):
    NONE = "none"
    BANNING = "banning"
    SIDE_PICK = "side_pick"


class MatchSide:
    CT = "ct"
    T = "t"


@dataclass
class PremierVetoState:
    phase: PremierVetoPhase = PremierVetoPhase.NONE
    mode: MatchMode | None = None
    queue_map_name: str = ""
    lobby_ids: list[int] = field(default_factory=list)
    team_alpha_ids: list[int] = field(default_factory=list)
    team_bravo_ids: list[int] = field(default_factory=list)
    alpha_captain_id: int | None = None
    bravo_captain_id: int | None = None
    remaining_maps: set[str] = field(default_factory=set)
    bans: list[tuple[str, str]] = field(default_factory=list)
    ban_turn_index: int = 0
    side_picker_team: str | None = None
    team1_side: str | None = None
    chosen_map: str | None = None
    initial_pool_size: int = 0

    def reset(self) -> None:
        self.phase = PremierVetoPhase.NONE
        self.mode = None
        self.queue_map_name = ""
        self.lobby_ids.clear()
        self.team_alpha_ids.clear()
        self.team_bravo_ids.clear()
        self.alpha_captain_id = None
        self.bravo_captain_id = None
        self.remaining_maps.clear()
        self.bans.clear()
        self.ban_turn_index = 0
        self.side_picker_team = None
        self.team1_side = None
        self.chosen_map = None
        self.initial_pool_size = 0

    def in_lobby(self, discord_id: int) -> bool:
        return discord_id in self.lobby_ids

    def start(
        self,
        mode: MatchMode,
        queue_map_name: str,
        lobby_ids: list[int],
        team_alpha_ids: list[int],
        team_bravo_ids: list[int],
        alpha_captain_id: int,
        bravo_captain_id: int,
        map_pool: frozenset[str],
    ) -> None:
        self.reset()
        self.mode = mode
        self.queue_map_name = queue_map_name
        self.lobby_ids = list(lobby_ids)
        self.team_alpha_ids = list(team_alpha_ids)
        self.team_bravo_ids = list(team_bravo_ids)
        self.alpha_captain_id = alpha_captain_id
        self.bravo_captain_id = bravo_captain_id
        self.remaining_maps = set(map_pool)
        self.initial_pool_size = len(map_pool)
        self.phase = PremierVetoPhase.BANNING

    def ban_turn_team(self) -> str:
        return CaptainTeam.ALPHA if self.ban_turn_index % 2 == 0 else CaptainTeam.BRAVO

    def captain_for_team(self, team: str) -> int | None:
        if team == CaptainTeam.ALPHA:
            return self.alpha_captain_id
        return self.bravo_captain_id

    def team_label(self, team: str) -> str:
        return "Team Alpha" if team == CaptainTeam.ALPHA else "Team Bravo"

    def side_label(self, side: str) -> str:
        return "CT" if side == MatchSide.CT else "T"

    def bans_required(self) -> int:
        return max(0, self.initial_pool_size - 1)

    def bans_remaining(self) -> int:
        return max(0, self.bans_required() - len(self.bans))

    def cast_ban(self, captain_id: int, map_id: str, allowed_maps: frozenset[str]) -> None:
        if self.phase != PremierVetoPhase.BANNING:
            raise ValueError("Map veto is not active for your queue.")
        if captain_id not in self.lobby_ids:
            raise ValueError("You are not part of the active match lobby.")

        turn_team = self.ban_turn_team()
        if captain_id != self.captain_for_team(turn_team):
            raise ValueError(
                f"Only the **{self.team_label(turn_team)}** captain can ban right now."
            )
        if map_id not in allowed_maps:
            raise ValueError("That map is not in the Premier veto pool.")
        if map_id not in self.remaining_maps:
            raise ValueError("That map is already banned.")

        self.remaining_maps.remove(map_id)
        self.bans.append((turn_team, map_id))
        self.ban_turn_index += 1

        if len(self.remaining_maps) == 1:
            self.chosen_map = next(iter(self.remaining_maps))
            self.side_picker_team = (
                CaptainTeam.BRAVO if turn_team == CaptainTeam.ALPHA else CaptainTeam.ALPHA
            )
            self.phase = PremierVetoPhase.SIDE_PICK

    def cast_side(self, captain_id: int, side: str) -> None:
        if self.phase != PremierVetoPhase.SIDE_PICK:
            raise ValueError("Side selection is not active for your queue.")
        if side not in {MatchSide.CT, MatchSide.T}:
            raise ValueError("Side must be **CT** or **T**.")
        if self.side_picker_team is None:
            raise ValueError("Side picker is not set for this veto.")
        if captain_id != self.captain_for_team(self.side_picker_team):
            raise ValueError(
                f"Only **{self.team_label(self.side_picker_team)}** captain can pick CT or T."
            )

        if self.side_picker_team == CaptainTeam.ALPHA:
            self.team1_side = side
        else:
            self.team1_side = MatchSide.T if side == MatchSide.CT else MatchSide.CT

        self.phase = PremierVetoPhase.NONE

    def veto_complete(self) -> bool:
        return (
            self.chosen_map is not None
            and self.team1_side is not None
            and self.phase == PremierVetoPhase.NONE
        )
