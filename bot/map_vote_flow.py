from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum


class MapVotePhase(str, Enum):
    NONE = "none"
    VOTING = "voting"


@dataclass
class MapVoteFlowState:
    phase: MapVotePhase = MapVotePhase.NONE
    lobby_ids: list[int] = field(default_factory=list)
    votes: dict[int, str] = field(default_factory=dict)

    def reset(self) -> None:
        self.phase = MapVotePhase.NONE
        self.lobby_ids.clear()
        self.votes.clear()

    def in_lobby(self, discord_id: int) -> bool:
        return discord_id in self.lobby_ids

    def start_voting(self, lobby_ids: list[int]) -> None:
        self.reset()
        self.phase = MapVotePhase.VOTING
        self.lobby_ids = list(lobby_ids)

    def voting_progress(self) -> tuple[int, int]:
        total = len(self.lobby_ids)
        done = sum(1 for voter in self.lobby_ids if voter in self.votes)
        return done, total

    def all_votes_cast(self) -> bool:
        if not self.lobby_ids:
            return False
        lobby = set(self.lobby_ids)
        return all(voter in self.votes for voter in lobby)

    def cast_vote(self, voter_id: int, map_id: str, allowed_maps: frozenset[str]) -> None:
        if self.phase != MapVotePhase.VOTING:
            raise ValueError("Map voting is not active for your queue.")
        if voter_id not in self.lobby_ids:
            raise ValueError("You are not part of the active match lobby.")
        if map_id not in allowed_maps:
            raise ValueError("That map is not available for voting.")

        self.votes[voter_id] = map_id

    def resolve_map(self, allowed_maps: frozenset[str]) -> str:
        valid_votes = [
            map_id for map_id in self.votes.values() if map_id in allowed_maps
        ]
        if not valid_votes:
            return random.choice(list(allowed_maps))

        counts: Counter[str] = Counter(valid_votes)
        max_votes = max(counts.values())
        top_maps = [map_id for map_id, count in counts.items() if count == max_votes]
        return random.choice(top_maps)
