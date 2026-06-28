"""Reaction-based lobby actions on the pinned #queue-status message."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from captain_flow import CaptainPhase, CaptainTeam
from config import MatchMode
from maps import ACTIVE_DUTY_MAPS, map_display_name
from matchmaker import Matchmaker
from premier_veto_flow import PremierVetoPhase

# Fixed veto slot order (must match PREMIER_VETO_MAP_ORDER / PREMIER_VETO_POOL in maps.py).
PREMIER_VETO_MAP_ORDER: tuple[str, ...] = tuple(
    game_map.map_id for game_map in ACTIVE_DUTY_MAPS
) + ("de_train", "de_cache")

READY_EMOJI = "✅"
UNREADY_EMOJI = "❌"

# Up to 10 lobby slots (5v5).
ALPHA_CAPTAIN_EMOJIS = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟")
BRAVO_CAPTAIN_EMOJIS = tuple(chr(0x24B6 + index) for index in range(10))  # Ⓐ … Ⓙ
DRAFT_PICK_EMOJIS = ALPHA_CAPTAIN_EMOJIS
MAP_BAN_EMOJIS = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣")
MAP_BAN_EMOJI_BY_MAP_ID: dict[str, str] = {
    map_id: MAP_BAN_EMOJIS[index]
    for index, map_id in enumerate(PREMIER_VETO_MAP_ORDER)
    if index < len(MAP_BAN_EMOJIS)
}
MAP_ID_BY_BAN_EMOJI: dict[str, str] = {
    emoji: map_id for map_id, emoji in MAP_BAN_EMOJI_BY_MAP_ID.items()
}
SIDE_CT_EMOJI = "🛡️"
SIDE_T_EMOJI = "⚔️"

BASE_QUEUE_EMOJIS = frozenset({READY_EMOJI, UNREADY_EMOJI})


def embed_heading(text: str, *, level: int = 2) -> str:
    """Discord embed markdown header — renders larger than body text."""
    marks = "#" * max(1, min(level, 3))
    return f"{marks} {text}"


def embed_item(text: str) -> str:
    """Single prominent lobby row (map, player pick, etc.)."""
    return f"# {text}"


class LobbyReactionKind(str, Enum):
    CAPTAIN_ALPHA = "captain_alpha"
    CAPTAIN_BRAVO = "captain_bravo"
    DRAFT_PICK = "draft_pick"
    MAP_BAN = "map_ban"
    SIDE_CT = "side_ct"
    SIDE_T = "side_t"


@dataclass(frozen=True)
class LobbyReactionAction:
    kind: LobbyReactionKind
    mode: MatchMode
    map_name: str
    target_id: int | None = None
    banned_map_id: str | None = None


@dataclass
class LobbyReactionPlan:
    mode: MatchMode | None = None
    map_name: str = ""
    actions: dict[str, LobbyReactionAction] = field(default_factory=dict)

    @property
    def emojis(self) -> frozenset[str]:
        action_emojis = frozenset(self.actions.keys())
        if len(action_emojis) + len(BASE_QUEUE_EMOJIS) > 20:
            # Captain vote uses up to 20 slot emojis; skip ready toggles until phase changes.
            return action_emojis
        return BASE_QUEUE_EMOJIS | action_emojis


def _active_lobby_mode(
    matchmaker: Matchmaker,
    default_map: str,
) -> tuple[MatchMode, str] | None:
    for mode in MatchMode:
        flow = matchmaker.get_captain_flow(mode, default_map)
        veto_flow = matchmaker.get_premier_veto_flow(mode, default_map)
        if flow.phase != CaptainPhase.NONE or veto_flow.phase != PremierVetoPhase.NONE:
            return mode, default_map
    return None


def _player_status_suffix(
    discord_id: int,
    *,
    ready: bool,
    flow,
    veto_flow,
) -> str:
    parts: list[str] = []
    if ready:
        parts.append("ready")
    else:
        parts.append("not ready")

    if flow.in_lobby(discord_id):
        if flow.phase == CaptainPhase.VOTING:
            if discord_id in flow.alpha_votes:
                parts.append("Alpha vote cast")
            if discord_id in flow.bravo_votes:
                parts.append("Bravo vote cast")
        elif flow.phase == CaptainPhase.DRAFTING:
            if discord_id in flow.team_alpha_ids:
                parts.append("Team Alpha")
            elif discord_id in flow.team_bravo_ids:
                parts.append("Team Bravo")
    elif veto_flow.in_lobby(discord_id):
        if discord_id in veto_flow.team_alpha_ids:
            parts.append("Team Alpha")
        elif discord_id in veto_flow.team_bravo_ids:
            parts.append("Team Bravo")
    elif flow.phase != CaptainPhase.NONE or veto_flow.phase != PremierVetoPhase.NONE:
        parts.append("joined after lobby locked")

    return " · ".join(parts)


def format_lobby_players_block(
    matchmaker: Matchmaker,
    mode: MatchMode,
    default_map: str,
) -> str | None:
    flow = matchmaker.get_captain_flow(mode, default_map)
    veto_flow = matchmaker.get_premier_veto_flow(mode, default_map)
    if flow.phase == CaptainPhase.NONE and veto_flow.phase == PremierVetoPhase.NONE:
        return None

    entries = matchmaker.get_mode_entries(mode, default_map)
    if not entries:
        return None

    entries_by_id = {entry.discord_id: entry for entry in entries}
    lobby_ids = flow.lobby_ids or veto_flow.lobby_ids
    lobby_set = set(lobby_ids)

    lines = [embed_heading("Queue players")]

    if flow.phase == CaptainPhase.VOTING and lobby_ids:
        lines.append("_Number = Alpha captain · Letter = Bravo captain (react on this message)_")
        for index, player_id in enumerate(lobby_ids):
            entry = entries_by_id.get(player_id)
            alpha = ALPHA_CAPTAIN_EMOJIS[index] if index < len(ALPHA_CAPTAIN_EMOJIS) else "?"
            bravo = BRAVO_CAPTAIN_EMOJIS[index] if index < len(BRAVO_CAPTAIN_EMOJIS) else "?"
            status = _player_status_suffix(
                player_id,
                ready=entry.ready if entry else False,
                flow=flow,
                veto_flow=veto_flow,
            )
            lines.append(embed_item(f"{alpha} / {bravo} <@{player_id}> — {status}"))

    elif flow.phase == CaptainPhase.DRAFTING:
        lines.append("_React with a number to draft that player (captain only)_")
        pick_index = {
            player_id: index
            for index, player_id in enumerate(flow.available_pick_ids)
        }
        for player_id in lobby_ids:
            entry = entries_by_id.get(player_id)
            status = _player_status_suffix(
                player_id,
                ready=entry.ready if entry else False,
                flow=flow,
                veto_flow=veto_flow,
            )
            if player_id in pick_index:
                emoji = DRAFT_PICK_EMOJIS[pick_index[player_id]]
                lines.append(embed_item(f"{emoji} <@{player_id}> — {status}"))
            else:
                lines.append(f"## <@{player_id}> — {status}")

        if flow.available_pick_ids:
            lines.append(embed_heading("Available to draft"))
            for i, player_id in enumerate(flow.available_pick_ids):
                if i >= len(DRAFT_PICK_EMOJIS):
                    break
                lines.append(embed_item(f"{DRAFT_PICK_EMOJIS[i]} <@{player_id}>"))

    elif veto_flow.phase == PremierVetoPhase.BANNING:
        lines.append("_Each map keeps its number — react to ban (captain only)_")
        for player_id in lobby_ids:
            entry = entries_by_id.get(player_id)
            status = _player_status_suffix(
                player_id,
                ready=entry.ready if entry else False,
                flow=flow,
                veto_flow=veto_flow,
            )
            lines.append(f"## <@{player_id}> — {status}")
        banned_ids = {map_id for _, map_id in veto_flow.bans}
        map_lines: list[str] = [embed_heading("Maps")]
        for map_id in PREMIER_VETO_MAP_ORDER:
            emoji = MAP_BAN_EMOJI_BY_MAP_ID.get(map_id)
            if emoji is None:
                continue
            name = map_display_name(map_id)
            if map_id in banned_ids:
                map_lines.append(embed_item(f"{emoji} ~~**{name}**~~ (`{map_id}`) — _banned_"))
            elif map_id in veto_flow.remaining_maps:
                map_lines.append(embed_item(f"{emoji} **{name}** (`{map_id}`)"))
        if len(map_lines) > 1:
            lines.extend(map_lines)

    elif veto_flow.phase == PremierVetoPhase.SIDE_PICK:
        lines.append(
            f"_Side picker: react {SIDE_CT_EMOJI} **CT** or {SIDE_T_EMOJI} **T**_"
        )
        for player_id in lobby_ids:
            entry = entries_by_id.get(player_id)
            status = _player_status_suffix(
                player_id,
                ready=entry.ready if entry else False,
                flow=flow,
                veto_flow=veto_flow,
            )
            lines.append(f"<@{player_id}> — {status}")

    else:
        for entry in entries:
            in_lobby = entry.discord_id in lobby_set
            prefix = "•" if in_lobby else "○"
            status = _player_status_suffix(
                entry.discord_id,
                ready=entry.ready,
                flow=flow,
                veto_flow=veto_flow,
            )
            lines.append(f"{prefix} <@{entry.discord_id}> — {status}")

    for entry in entries:
        if entry.discord_id in lobby_set:
            continue
        status = _player_status_suffix(
            entry.discord_id,
            ready=entry.ready,
            flow=flow,
            veto_flow=veto_flow,
        )
        lines.append(f"○ <@{entry.discord_id}> — {status} _(not in lobby)_")

    return "\n".join(lines)


def build_lobby_reaction_plan(
    matchmaker: Matchmaker,
    default_map: str,
) -> LobbyReactionPlan:
    plan = LobbyReactionPlan()
    active = _active_lobby_mode(matchmaker, default_map)
    if active is None:
        return plan

    mode, map_name = active
    plan.mode = mode
    plan.map_name = map_name

    flow = matchmaker.get_captain_flow(mode, map_name)
    veto_flow = matchmaker.get_premier_veto_flow(mode, map_name)

    if flow.phase == CaptainPhase.VOTING:
        for index, player_id in enumerate(flow.lobby_ids):
            if index < len(ALPHA_CAPTAIN_EMOJIS):
                emoji = ALPHA_CAPTAIN_EMOJIS[index]
                plan.actions[emoji] = LobbyReactionAction(
                    LobbyReactionKind.CAPTAIN_ALPHA,
                    mode,
                    map_name,
                    target_id=player_id,
                )
            if index < len(BRAVO_CAPTAIN_EMOJIS):
                emoji = BRAVO_CAPTAIN_EMOJIS[index]
                plan.actions[emoji] = LobbyReactionAction(
                    LobbyReactionKind.CAPTAIN_BRAVO,
                    mode,
                    map_name,
                    target_id=player_id,
                )

    elif flow.phase == CaptainPhase.DRAFTING:
        for index, player_id in enumerate(flow.available_pick_ids):
            if index < len(DRAFT_PICK_EMOJIS):
                emoji = DRAFT_PICK_EMOJIS[index]
                plan.actions[emoji] = LobbyReactionAction(
                    LobbyReactionKind.DRAFT_PICK,
                    mode,
                    map_name,
                    target_id=player_id,
                )

    elif veto_flow.phase == PremierVetoPhase.BANNING:
        for map_id in veto_flow.remaining_maps:
            emoji = MAP_BAN_EMOJI_BY_MAP_ID.get(map_id)
            if emoji is None:
                continue
            plan.actions[emoji] = LobbyReactionAction(
                LobbyReactionKind.MAP_BAN,
                mode,
                map_name,
                banned_map_id=map_id,
            )

    elif veto_flow.phase == PremierVetoPhase.SIDE_PICK:
        plan.actions[SIDE_CT_EMOJI] = LobbyReactionAction(
            LobbyReactionKind.SIDE_CT,
            mode,
            map_name,
        )
        plan.actions[SIDE_T_EMOJI] = LobbyReactionAction(
            LobbyReactionKind.SIDE_T,
            mode,
            map_name,
        )

    return plan


def resolve_lobby_reaction(
    emoji: str,
    matchmaker: Matchmaker,
    default_map: str,
) -> LobbyReactionAction | None:
    plan = build_lobby_reaction_plan(matchmaker, default_map)
    return plan.actions.get(emoji)


def alpha_vote_emojis() -> frozenset[str]:
    return frozenset(ALPHA_CAPTAIN_EMOJIS)


def bravo_vote_emojis() -> frozenset[str]:
    return frozenset(BRAVO_CAPTAIN_EMOJIS)
