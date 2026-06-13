from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field

from captain_flow import CaptainFlowState, CaptainPhase, CaptainTeam, _tally_winner
from config import MatchMode
from map_vote_flow import MapVoteFlowState
from maps import PREMIER_VETO_POOL, map_display_name
from matchzy import ActiveMatch, QueuedPlayer
from premier_veto_flow import MatchSide, PremierVetoPhase, PremierVetoState


@dataclass
class QueueEntry:
    discord_id: int
    discord_name: str
    steam_id: str
    map_name: str
    ready: bool = False


@dataclass
class Matchmaker:
    default_map: str
    next_match_id: int = 1
    queues: dict[tuple[MatchMode, str], list[QueueEntry]] = field(
        default_factory=lambda: defaultdict(list)
    )
    active_matches: dict[str, ActiveMatch] = field(default_factory=dict)
    captain_flows: dict[tuple[MatchMode, str], CaptainFlowState] = field(
        default_factory=lambda: defaultdict(CaptainFlowState)
    )
    map_vote_flows: dict[tuple[MatchMode, str], MapVoteFlowState] = field(
        default_factory=lambda: defaultdict(MapVoteFlowState)
    )
    premier_veto_flows: dict[tuple[MatchMode, str], PremierVetoState] = field(
        default_factory=lambda: defaultdict(PremierVetoState)
    )

    def _allocate_match_id(self) -> str:
        match_id = str(self.next_match_id)
        self.next_match_id += 1
        return match_id

    def queue_key(self, mode: MatchMode, map_name: str | None = None) -> tuple[MatchMode, str]:
        return mode, map_name or self.default_map

    def get_mode_entries(self, mode: MatchMode, map_name: str | None = None) -> list[QueueEntry]:
        return list(self.queues[self.queue_key(mode, map_name)])

    def queue_size(self, mode: MatchMode, map_name: str | None = None) -> int:
        if map_name is not None:
            return len(self.queues[(mode, map_name)])
        return sum(len(entries) for (queued_mode, _), entries in self.queues.items() if queued_mode == mode)

    def is_queued(self, discord_id: int) -> tuple[MatchMode, str] | None:
        for (mode, map_name), entries in self.queues.items():
            if any(entry.discord_id == discord_id for entry in entries):
                return mode, map_name
        return None

    def get_entry(self, discord_id: int) -> tuple[tuple[MatchMode, str], QueueEntry] | None:
        for queue_key, entries in self.queues.items():
            for entry in entries:
                if entry.discord_id == discord_id:
                    return queue_key, entry
        return None

    def enter_queue(
        self,
        mode: MatchMode,
        discord_id: int,
        discord_name: str,
        steam_id: str,
        map_name: str | None = None,
    ) -> QueueEntry:
        existing = self.is_queued(discord_id)
        if existing is not None:
            existing_mode, existing_map = existing
            if existing_mode == mode and existing_map == (map_name or self.default_map):
                entry_data = self.get_entry(discord_id)
                if entry_data is not None:
                    return entry_data[1]
            raise ValueError(
                f"You are already in the {existing_mode.label} queue on `{existing_map}`."
            )

        selected_map = map_name or self.default_map
        entry = QueueEntry(
            discord_id=discord_id,
            discord_name=discord_name,
            steam_id=steam_id,
            map_name=selected_map,
            ready=False,
        )
        self.queues[(mode, selected_map)].append(entry)
        return entry

    def leave_queue(self, discord_id: int) -> tuple[MatchMode, str]:
        for (mode, map_name), entries in self.queues.items():
            for index, entry in enumerate(entries):
                if entry.discord_id == discord_id:
                    entries.pop(index)
                    flow = self.get_captain_flow(mode, map_name)
                    if flow.in_lobby(discord_id):
                        flow.reset()
                    veto_flow = self.get_premier_veto_flow(mode, map_name)
                    if veto_flow.in_lobby(discord_id):
                        veto_flow.reset()
                    map_flow = self.get_map_vote_flow(mode, map_name)
                    if map_flow.in_lobby(discord_id):
                        map_flow.reset()
                    self.maybe_start_captain_flow(mode, map_name)
                    if mode == MatchMode.ONE_V_ONE:
                        self.maybe_start_premier_veto_1v1(mode, map_name)
                    return mode, map_name
        raise ValueError("You are not in any queue.")

    def get_captain_flow(
        self,
        mode: MatchMode,
        map_name: str | None = None,
    ) -> CaptainFlowState:
        return self.captain_flows[self.queue_key(mode, map_name)]

    def get_map_vote_flow(
        self,
        mode: MatchMode,
        map_name: str | None = None,
    ) -> MapVoteFlowState:
        return self.map_vote_flows[self.queue_key(mode, map_name)]

    def get_premier_veto_flow(
        self,
        mode: MatchMode,
        map_name: str | None = None,
    ) -> PremierVetoState:
        return self.premier_veto_flows[self.queue_key(mode, map_name)]

    def captains_required(self, mode: MatchMode) -> bool:
        return mode != MatchMode.ONE_V_ONE

    def map_vote_required(self, mode: MatchMode) -> bool:
        return False

    def premier_veto_required(self, mode: MatchMode) -> bool:
        return True

    def get_ready_entries(self, mode: MatchMode, map_name: str) -> list[QueueEntry]:
        return [entry for entry in self.queues[(mode, map_name)] if entry.ready]

    def maybe_start_captain_flow(
        self,
        mode: MatchMode,
        map_name: str,
    ) -> tuple[CaptainFlowState | None, bool]:
        if not self.captains_required(mode):
            return None, False

        flow = self.get_captain_flow(mode, map_name)
        if flow.phase != CaptainPhase.NONE:
            return flow, False

        ready_entries = self.get_ready_entries(mode, map_name)
        if len(ready_entries) < mode.total_players:
            return None, False

        lobby_ids = [entry.discord_id for entry in ready_entries[: mode.total_players]]
        flow.start_voting(lobby_ids)
        return flow, True

    def maybe_start_premier_veto_1v1(
        self,
        mode: MatchMode,
        map_name: str,
    ) -> tuple[PremierVetoState | None, bool]:
        if mode != MatchMode.ONE_V_ONE:
            return None, False

        flow = self.get_premier_veto_flow(mode, map_name)
        if flow.phase != PremierVetoPhase.NONE:
            return flow, False

        ready_entries = self.get_ready_entries(mode, map_name)
        if len(ready_entries) < mode.total_players:
            return None, False

        lobby_ids = [entry.discord_id for entry in ready_entries[: mode.total_players]]
        alpha_id, bravo_id = lobby_ids[0], lobby_ids[1]
        flow.start(
            mode,
            map_name,
            lobby_ids,
            [alpha_id],
            [bravo_id],
            alpha_id,
            bravo_id,
            PREMIER_VETO_POOL,
        )
        return flow, True

    def cast_premier_ban(
        self,
        discord_id: int,
        banned_map_id: str,
    ) -> tuple[MatchMode, str, str, ActiveMatch | None]:
        entry_data = self.get_entry(discord_id)
        if entry_data is None:
            raise ValueError("Join a **Queue » 1v1 / 2v2 / 5v5** voice channel first.")
        (mode, map_name), entry = entry_data
        if not entry.ready:
            raise ValueError("You must be **Ready** before banning a map.")

        flow = self.get_premier_veto_flow(mode, map_name)
        if flow.phase == PremierVetoPhase.NONE and mode == MatchMode.ONE_V_ONE:
            self.maybe_start_premier_veto_1v1(mode, map_name)
        if flow.phase != PremierVetoPhase.BANNING:
            raise ValueError("Map veto is not active for your queue.")

        flow.cast_ban(discord_id, banned_map_id, PREMIER_VETO_POOL)
        banned_name = map_display_name(banned_map_id)
        turn_team = flow.ban_turn_team() if flow.phase == PremierVetoPhase.BANNING else flow.bans[-1][0]
        message = f"Banned **{banned_name}** (`{banned_map_id}`)."

        if flow.phase == PremierVetoPhase.SIDE_PICK:
            chosen_name = map_display_name(flow.chosen_map or "")
            picker_label = flow.team_label(flow.side_picker_team or CaptainTeam.ALPHA)
            message = (
                f"Banned **{banned_name}**.\n"
                f"Map veto complete — playing **{chosen_name}** (`{flow.chosen_map}`).\n"
                f"**{picker_label}** captain: pick **CT** or **T** with **Pick Side**."
            )
            return mode, map_name, message, None

        next_team = flow.team_label(turn_team)
        message = (
            f"{message}\n"
            f"**{next_team}** captain bans next ({flow.bans_remaining()} ban(s) left)."
        )
        return mode, map_name, message, None

    def cast_premier_side(
        self,
        discord_id: int,
        side: str,
    ) -> tuple[MatchMode, str, str, ActiveMatch | None]:
        entry_data = self.get_entry(discord_id)
        if entry_data is None:
            raise ValueError("Join a **Queue » 1v1 / 2v2 / 5v5** voice channel first.")
        (mode, map_name), entry = entry_data
        if not entry.ready:
            raise ValueError("You must be **Ready** before picking a side.")

        flow = self.get_premier_veto_flow(mode, map_name)
        if flow.phase != PremierVetoPhase.SIDE_PICK:
            raise ValueError("Side selection is not active for your queue.")

        flow.cast_side(discord_id, side)
        if not flow.veto_complete():
            raise ValueError("Side selection could not be completed.")

        alpha_side = flow.team1_side or MatchSide.CT
        alpha_label = "CT" if alpha_side == MatchSide.CT else "T"
        bravo_label = "T" if alpha_side == MatchSide.CT else "CT"
        chosen_name = map_display_name(flow.chosen_map or map_name)
        message = (
            f"**Team Alpha** starts **{alpha_label}**, **Team Bravo** starts **{bravo_label}** "
            f"on **{chosen_name}**."
        )
        match = self._create_match_from_premier_veto(mode, map_name, flow)
        return mode, map_name, message, match

    def admin_reset_premier_veto(self, mode: MatchMode, map_name: str | None = None) -> str:
        selected_map = map_name or self.default_map
        self.get_premier_veto_flow(mode, selected_map).reset()
        return (
            f"Premier map veto for {mode.label} on `{selected_map}` has been reset. "
            "Ready players can start again in #queue-status."
        )

    def admin_reset_map_vote(self, mode: MatchMode, map_name: str | None = None) -> str:
        return self.admin_reset_premier_veto(mode, map_name)

    def cast_captain_vote(
        self,
        discord_id: int,
        team: str,
        candidate_id: int,
    ) -> tuple[MatchMode, str, str, ActiveMatch | None]:
        entry_data = self.get_entry(discord_id)
        if entry_data is None:
            raise ValueError("Join a **Queue » 1v1 / 2v2 / 5v5** voice channel first.")
        (mode, map_name), entry = entry_data
        if not self.captains_required(mode):
            raise ValueError("Captain voting is only used for 2v2 and 5v5 queues.")
        if not entry.ready:
            raise ValueError("You must be **Ready** before voting for captains.")

        flow = self.get_captain_flow(mode, map_name)
        if flow.phase == CaptainPhase.NONE:
            self.maybe_start_captain_flow(mode, map_name)
        if flow.phase != CaptainPhase.VOTING:
            raise ValueError("Captain voting is not active for your queue.")

        flow.cast_vote(discord_id, team, candidate_id)
        team_label = "Team Alpha" if team == CaptainTeam.ALPHA else "Team Bravo"
        message = f"Your vote for **{team_label}** captain was recorded."

        if flow.all_votes_cast():
            flow.finalize_captains()
            flow.start_draft(mode)
            alpha_id, bravo_id = flow.team_alpha_captain_id, flow.team_bravo_captain_id
            message = (
                f"Your vote for **{team_label}** captain was recorded.\n"
                f"Voting finished — <@{alpha_id}> leads **Team Alpha**, "
                f"<@{bravo_id}> leads **Team Bravo**. Draft starting now."
            )
            match = self.try_complete_draft(mode, map_name)
            return mode, map_name, message, match

        return mode, map_name, message, None

    def draft_pick(
        self,
        captain_id: int,
        picked_id: int,
    ) -> tuple[MatchMode, str, str, ActiveMatch | None]:
        entry_data = self.get_entry(captain_id)
        if entry_data is None:
            raise ValueError("You are not in a queue.")
        (mode, map_name), _ = entry_data

        flow = self.get_captain_flow(mode, map_name)
        if flow.phase != CaptainPhase.DRAFTING:
            raise ValueError("The player draft is not active for your queue.")

        picking_team = flow.pick_turn
        completed = flow.apply_pick(captain_id, picked_id, mode.players_per_team)
        picked_label = f"<@{picked_id}>"
        picked_for = "Team Alpha" if picking_team == CaptainTeam.ALPHA else "Team Bravo"

        if completed:
            message = (
                f"You picked {picked_label} for **{picked_for}**. "
                "Draft complete — **Premier map veto** starting now."
            )
            self._begin_premier_veto_from_draft(mode, map_name, flow)
            return mode, map_name, message, None

        next_team = "Team Alpha" if flow.pick_turn == CaptainTeam.ALPHA else "Team Bravo"
        message = f"You picked {picked_label} for **{picked_for}**. **{next_team}** picks next."
        return mode, map_name, message, None

    def admin_reset_captains(self, mode: MatchMode, map_name: str | None = None) -> str:
        if not self.captains_required(mode):
            raise ValueError("Captain selection is only used for 2v2 and 5v5 queues.")
        selected_map = map_name or self.default_map
        self.get_captain_flow(mode, selected_map).reset()
        self.get_premier_veto_flow(mode, selected_map).reset()
        return (
            f"Captain vote, draft, and Premier veto for {mode.label} on `{selected_map}` "
            "have been reset. Ready players can vote again in #queue-status."
        )

    def admin_set_captain(
        self,
        mode: MatchMode,
        discord_id: int,
        team: str,
        map_name: str | None = None,
    ) -> tuple[str, ActiveMatch | None]:
        if not self.captains_required(mode):
            raise ValueError("Captain selection is only used for 2v2 and 5v5 queues.")
        if team not in (CaptainTeam.ALPHA, CaptainTeam.BRAVO):
            raise ValueError("Team must be `alpha` or `bravo`.")

        selected_map = map_name or self.default_map
        entry_data = self.get_entry(discord_id)
        if entry_data is None:
            raise ValueError("That player is not in the queue.")
        entry_mode, entry_map = entry_data[0]
        if entry_mode != mode or entry_map != selected_map:
            raise ValueError(
                f"That player is not in the {mode.label} queue on `{selected_map}`."
            )

        ready_entries = self.get_ready_entries(mode, selected_map)
        if len(ready_entries) < mode.total_players:
            raise ValueError(
                f"Need at least {mode.total_players} ready players before assigning captains."
            )

        flow = self.get_captain_flow(mode, selected_map)
        flow.reset()
        lobby_ids = [entry.discord_id for entry in ready_entries[: mode.total_players]]
        flow.lobby_ids = list(lobby_ids)
        flow.phase = CaptainPhase.DRAFTING

        other_ids = set(lobby_ids) - {discord_id}
        if team == CaptainTeam.ALPHA:
            flow.team_alpha_captain_id = discord_id
            flow.team_bravo_captain_id = _tally_winner({}, other_ids)
            team_label = "Team Alpha"
        else:
            flow.team_bravo_captain_id = discord_id
            flow.team_alpha_captain_id = _tally_winner({}, other_ids)
            team_label = "Team Bravo"

        flow.start_draft(mode)
        message = (
            f"Captain process restarted. <@{discord_id}> is **{team_label}** captain. "
            "Captains pick players in turn via **Pick Player** in #queue-status."
        )
        match = self.try_complete_draft(mode, selected_map)
        if match is not None:
            message = (
                f"Captain process restarted. <@{discord_id}> is **{team_label}** captain. "
                "Match is starting."
            )
        else:
            veto_flow = self.get_premier_veto_flow(mode, selected_map)
            if veto_flow.phase == PremierVetoPhase.BANNING:
                message = (
                    f"Captain process restarted. <@{discord_id}> is **{team_label}** captain. "
                    "Draft complete — **Premier map veto** started."
                )
        return message, match

    def _on_lobby_member_change(self, mode: MatchMode, map_name: str, discord_id: int) -> None:
        veto_flow = self.get_premier_veto_flow(mode, map_name)
        if veto_flow.phase != PremierVetoPhase.NONE and veto_flow.in_lobby(discord_id):
            veto_flow.reset()
            if mode == MatchMode.ONE_V_ONE:
                self.maybe_start_premier_veto_1v1(mode, map_name)
            elif self.captains_required(mode):
                self.get_captain_flow(mode, map_name).reset()
                self.maybe_start_captain_flow(mode, map_name)
            return

        if self.captains_required(mode):
            flow = self.get_captain_flow(mode, map_name)
            if flow.phase == CaptainPhase.NONE:
                return
            if flow.in_lobby(discord_id):
                flow.reset()
                self.maybe_start_captain_flow(mode, map_name)

    def set_ready(self, discord_id: int, ready: bool) -> tuple[MatchMode, str, ActiveMatch | None, bool]:
        entry_data = self.get_entry(discord_id)
        if entry_data is None:
            raise ValueError("Join a **Queue » 1v1 / 2v2 / 5v5** voice channel first.")
        (mode, map_name), entry = entry_data
        entry.ready = ready
        if not ready:
            self._on_lobby_member_change(mode, map_name, discord_id)
            return mode, map_name, None, False

        if self.captains_required(mode):
            flow, voting_started = self.maybe_start_captain_flow(mode, map_name)
            if flow is not None and flow.phase == CaptainPhase.VOTING:
                return mode, map_name, None, voting_started
            if flow is not None and flow.phase == CaptainPhase.DRAFTING:
                return mode, map_name, self.try_complete_draft(mode, map_name), False
            return mode, map_name, None, False

        if mode == MatchMode.ONE_V_ONE:
            flow, voting_started = self.maybe_start_premier_veto_1v1(mode, map_name)
            if flow is not None and flow.phase == PremierVetoPhase.BANNING:
                return mode, map_name, None, voting_started
            return mode, map_name, None, False

        return mode, map_name, self.try_create_match(mode, map_name), False

    def try_complete_draft(self, mode: MatchMode, map_name: str) -> ActiveMatch | None:
        flow = self.get_captain_flow(mode, map_name)
        if flow.phase != CaptainPhase.DRAFTING:
            return None
        if not flow.draft_complete(mode.players_per_team):
            return None
        self._begin_premier_veto_from_draft(mode, map_name, flow)
        return None

    def _begin_premier_veto_from_draft(
        self,
        mode: MatchMode,
        map_name: str,
        captain_flow: CaptainFlowState,
    ) -> None:
        veto_flow = self.get_premier_veto_flow(mode, map_name)
        if veto_flow.phase != PremierVetoPhase.NONE:
            return

        alpha_id = captain_flow.team_alpha_captain_id
        bravo_id = captain_flow.team_bravo_captain_id
        if alpha_id is None or bravo_id is None:
            captain_flow.reset()
            return

        veto_flow.start(
            mode,
            map_name,
            list(captain_flow.lobby_ids),
            list(captain_flow.team_alpha_ids),
            list(captain_flow.team_bravo_ids),
            alpha_id,
            bravo_id,
            PREMIER_VETO_POOL,
        )
        captain_flow.reset()

    def _create_match_from_premier_veto(
        self,
        mode: MatchMode,
        queue_map_name: str,
        flow: PremierVetoState,
    ) -> ActiveMatch | None:
        if not flow.veto_complete():
            return None

        queue_key = (mode, queue_map_name)
        entries_by_id = {entry.discord_id: entry for entry in self.queues[queue_key]}
        try:
            team1_entries = [entries_by_id[player_id] for player_id in flow.team_alpha_ids]
            team2_entries = [entries_by_id[player_id] for player_id in flow.team_bravo_ids]
        except KeyError:
            flow.reset()
            return None

        lobby_ids = set(flow.lobby_ids)
        self.queues[queue_key] = [
            entry for entry in self.queues[queue_key] if entry.discord_id not in lobby_ids
        ]
        chosen_map = flow.chosen_map or queue_map_name
        team1_side = flow.team1_side or MatchSide.CT
        flow.reset()
        return self._create_match_from_teams(
            mode,
            chosen_map,
            team1_entries,
            team2_entries,
            team1_side=team1_side,
        )

    def try_create_match(self, mode: MatchMode, map_name: str) -> ActiveMatch | None:
        if self.captains_required(mode):
            return self.try_complete_draft(mode, map_name)
        if mode == MatchMode.ONE_V_ONE:
            return None

        queue_key = (mode, map_name)
        ready_entries = self.get_ready_entries(mode, map_name)
        if len(ready_entries) < mode.total_players:
            return None

        selected = ready_entries[: mode.total_players]
        selected_ids = {entry.discord_id for entry in selected}
        self.queues[queue_key] = [
            entry for entry in self.queues[queue_key] if entry.discord_id not in selected_ids
        ]
        return self._create_match_from_entries(mode, map_name, selected)

    def _create_match_from_draft(
        self,
        mode: MatchMode,
        map_name: str,
        flow: CaptainFlowState,
    ) -> ActiveMatch | None:
        self._begin_premier_veto_from_draft(mode, map_name, flow)
        return None

    def _create_match_from_map_vote(
        self,
        mode: MatchMode,
        queue_map_name: str,
        flow: MapVoteFlowState,
        chosen_map: str,
    ) -> ActiveMatch | None:
        queue_key = (mode, queue_map_name)
        ready_entries = self.get_ready_entries(mode, queue_map_name)
        lobby_ids = set(flow.lobby_ids)
        selected = [
            entry for entry in ready_entries if entry.discord_id in lobby_ids
        ][: mode.total_players]
        if len(selected) < mode.total_players:
            flow.reset()
            return None

        selected_ids = {entry.discord_id for entry in selected}
        self.queues[queue_key] = [
            entry for entry in self.queues[queue_key] if entry.discord_id not in selected_ids
        ]
        flow.reset()
        return self._create_match_from_entries(mode, chosen_map, selected)

    def get_match(self, match_id: str) -> ActiveMatch | None:
        return self.active_matches.get(match_id)

    def finish_match(self, match_id: str) -> None:
        self.active_matches.pop(match_id, None)

    def restore_match_players_to_queue(self, match: ActiveMatch) -> None:
        queue_map = self.default_map
        queue_key = (match.mode, queue_map)
        existing_ids = {entry.discord_id for entry in self.queues[queue_key]}
        for player in match.team1 + match.team2:
            if player.discord_id in existing_ids:
                continue
            self.queues[queue_key].append(
                QueueEntry(
                    discord_id=player.discord_id,
                    discord_name=player.discord_name,
                    steam_id=player.steam_id,
                    map_name=queue_map,
                    ready=True,
                )
            )
        self.finish_match(match.match_id)
        if self.captains_required(match.mode):
            self.maybe_start_captain_flow(match.mode, queue_map)
        elif self.premier_veto_required(match.mode):
            self.maybe_start_premier_veto_1v1(match.mode, queue_map)

    def _create_match_from_entries(
        self,
        mode: MatchMode,
        map_name: str,
        entries: list[QueueEntry],
    ) -> ActiveMatch:
        random.shuffle(entries)
        midpoint = mode.players_per_team
        return self._create_match_from_teams(
            mode,
            map_name,
            entries[:midpoint],
            entries[midpoint:],
        )

    def _create_match_from_teams(
        self,
        mode: MatchMode,
        map_name: str,
        team1_entries: list[QueueEntry],
        team2_entries: list[QueueEntry],
        *,
        team1_side: str = MatchSide.CT,
    ) -> ActiveMatch:
        team1 = [
            QueuedPlayer(
                discord_id=entry.discord_id,
                discord_name=entry.discord_name,
                steam_id=entry.steam_id,
            )
            for entry in team1_entries
        ]
        team2 = [
            QueuedPlayer(
                discord_id=entry.discord_id,
                discord_name=entry.discord_name,
                steam_id=entry.steam_id,
            )
            for entry in team2_entries
        ]

        match_id = self._allocate_match_id()
        match = ActiveMatch(
            match_id=match_id,
            mode=mode,
            map_name=map_name,
            team1=team1,
            team2=team2,
            team1_side=team1_side,
        )
        self.active_matches[match_id] = match
        return match

    def get_lobby_candidates(
        self,
        mode: MatchMode,
        map_name: str,
    ) -> list[tuple[int, str]]:
        flow = self.get_captain_flow(mode, map_name)
        if not flow.lobby_ids:
            return []

        entries_by_id = {
            entry.discord_id: entry
            for entry in self.get_mode_entries(mode, map_name)
        }
        return [
            (player_id, entries_by_id[player_id].discord_name)
            for player_id in flow.lobby_ids
            if player_id in entries_by_id
        ]

    def get_draft_candidates(
        self,
        mode: MatchMode,
        map_name: str,
    ) -> list[tuple[int, str]]:
        flow = self.get_captain_flow(mode, map_name)
        if flow.phase != CaptainPhase.DRAFTING:
            return []

        entries_by_id = {
            entry.discord_id: entry
            for entry in self.get_mode_entries(mode, map_name)
        }
        return [
            (player_id, entries_by_id[player_id].discord_name)
            for player_id in flow.available_pick_ids
            if player_id in entries_by_id
        ]

    def all_queued_players_ready(self, mode: MatchMode, map_name: str) -> bool:
        entries = self.get_mode_entries(mode, map_name)
        return bool(entries) and all(entry.ready for entry in entries)

    def clear_queue(self, mode: MatchMode, map_name: str) -> list[int]:
        queue_key = (mode, map_name)
        removed_ids = [entry.discord_id for entry in self.queues[queue_key]]
        self.queues[queue_key].clear()
        self.get_captain_flow(mode, map_name).reset()
        self.get_map_vote_flow(mode, map_name).reset()
        self.get_premier_veto_flow(mode, map_name).reset()
        return removed_ids

    def queue_summary(self) -> str:
        lines = ["**Queue status**"]
        for mode in MatchMode:
            entries = self.get_mode_entries(mode)
            ready_count = sum(1 for entry in entries if entry.ready)
            lines.append(
                f"- {mode.label}: {ready_count}/{mode.total_players} ready "
                f"({len(entries)} in queue)"
            )
        if self.active_matches:
            lines.append(f"- Active matches: {len(self.active_matches)}")
        return "\n".join(lines)
