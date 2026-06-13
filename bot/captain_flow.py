from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum

from config import MatchMode


class CaptainTeam:
    ALPHA = "alpha"
    BRAVO = "bravo"


class CaptainPhase(str, Enum):
    NONE = "none"
    VOTING = "voting"
    DRAFTING = "drafting"


@dataclass
class CaptainFlowState:
    phase: CaptainPhase = CaptainPhase.NONE
    lobby_ids: list[int] = field(default_factory=list)
    alpha_votes: dict[int, int] = field(default_factory=dict)
    bravo_votes: dict[int, int] = field(default_factory=dict)
    team_alpha_captain_id: int | None = None
    team_bravo_captain_id: int | None = None
    team_alpha_ids: list[int] = field(default_factory=list)
    team_bravo_ids: list[int] = field(default_factory=list)
    available_pick_ids: list[int] = field(default_factory=list)
    pick_turn: str = CaptainTeam.ALPHA

    def reset(self) -> None:
        self.phase = CaptainPhase.NONE
        self.lobby_ids.clear()
        self.alpha_votes.clear()
        self.bravo_votes.clear()
        self.team_alpha_captain_id = None
        self.team_bravo_captain_id = None
        self.team_alpha_ids.clear()
        self.team_bravo_ids.clear()
        self.available_pick_ids.clear()
        self.pick_turn = CaptainTeam.ALPHA

    def in_lobby(self, discord_id: int) -> bool:
        return discord_id in self.lobby_ids

    def current_picker_id(self) -> int | None:
        if self.phase != CaptainPhase.DRAFTING:
            return None
        if self.pick_turn == CaptainTeam.ALPHA:
            return self.team_alpha_captain_id
        return self.team_bravo_captain_id

    def draft_complete(self, players_per_team: int) -> bool:
        return (
            len(self.team_alpha_ids) == players_per_team
            and len(self.team_bravo_ids) == players_per_team
        )

    def voting_progress(self) -> tuple[int, int, int]:
        total = len(self.lobby_ids)
        alpha_done = sum(1 for voter in self.lobby_ids if voter in self.alpha_votes)
        bravo_done = sum(1 for voter in self.lobby_ids if voter in self.bravo_votes)
        return alpha_done, bravo_done, total

    def all_votes_cast(self) -> bool:
        if not self.lobby_ids:
            return False
        lobby = set(self.lobby_ids)
        return all(voter in self.alpha_votes and voter in self.bravo_votes for voter in lobby)

    def start_voting(self, lobby_ids: list[int]) -> None:
        self.reset()
        self.phase = CaptainPhase.VOTING
        self.lobby_ids = list(lobby_ids)

    def start_draft(self, mode: MatchMode) -> None:
        self.phase = CaptainPhase.DRAFTING
        self.team_alpha_ids = [self.team_alpha_captain_id]
        self.team_bravo_ids = [self.team_bravo_captain_id]
        self.available_pick_ids = [
            player_id
            for player_id in self.lobby_ids
            if player_id
            not in {self.team_alpha_captain_id, self.team_bravo_captain_id}
        ]
        self.pick_turn = CaptainTeam.ALPHA

    def cast_vote(
        self,
        voter_id: int,
        team: str,
        candidate_id: int,
    ) -> None:
        if self.phase != CaptainPhase.VOTING:
            raise ValueError("Captain voting is not active for your queue.")
        if voter_id not in self.lobby_ids:
            raise ValueError("You are not part of the active match lobby.")
        if candidate_id not in self.lobby_ids:
            raise ValueError("You can only vote for players in the current lobby.")

        if team == CaptainTeam.ALPHA:
            self.alpha_votes[voter_id] = candidate_id
        elif team == CaptainTeam.BRAVO:
            self.bravo_votes[voter_id] = candidate_id
        else:
            raise ValueError("Team must be `alpha` or `bravo`.")

    def finalize_captains(self) -> tuple[int, int]:
        lobby = set(self.lobby_ids)
        alpha_captain = _tally_winner(self.alpha_votes, lobby)
        if alpha_captain is None:
            alpha_captain = random.choice(list(lobby))
        bravo_pool = lobby - {alpha_captain}
        bravo_votes = {
            voter: candidate
            for voter, candidate in self.bravo_votes.items()
            if candidate in bravo_pool
        }
        bravo_captain = _tally_winner(bravo_votes, bravo_pool)
        if bravo_captain is None:
            bravo_captain = random.choice(list(bravo_pool))

        self.team_alpha_captain_id = alpha_captain
        self.team_bravo_captain_id = bravo_captain
        return alpha_captain, bravo_captain

    def apply_pick(self, captain_id: int, picked_id: int, players_per_team: int) -> bool:
        if self.phase != CaptainPhase.DRAFTING:
            raise ValueError("The player draft is not active.")
        if captain_id != self.current_picker_id():
            raise ValueError("It is not your turn to pick.")
        if picked_id not in self.available_pick_ids:
            raise ValueError("That player is not available to pick.")

        if self.pick_turn == CaptainTeam.ALPHA:
            self.team_alpha_ids.append(picked_id)
        else:
            self.team_bravo_ids.append(picked_id)

        self.available_pick_ids.remove(picked_id)

        if self.draft_complete(players_per_team):
            return True

        if self.pick_turn == CaptainTeam.ALPHA:
            self.pick_turn = CaptainTeam.BRAVO
        else:
            self.pick_turn = CaptainTeam.ALPHA
        return False


def _tally_winner(votes: dict[int, int], allowed_candidates: set[int]) -> int | None:
    if not allowed_candidates:
        return None

    counts: Counter[int] = Counter()
    for candidate in votes.values():
        if candidate in allowed_candidates:
            counts[candidate] += 1

    if not counts:
        return random.choice(list(allowed_candidates))

    max_votes = max(counts.values())
    top_candidates = [candidate for candidate, count in counts.items() if count == max_votes]
    return random.choice(top_candidates)
