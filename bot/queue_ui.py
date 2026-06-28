from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from captain_flow import CaptainFlowState, CaptainPhase, CaptainTeam
from config import MatchMode
from guild_setup import VOICE_CHANNEL_NAMES
from lobby_reactions import (
    READY_EMOJI,
    UNREADY_EMOJI,
    SIDE_CT_EMOJI,
    SIDE_T_EMOJI,
    embed_heading,
    embed_item,
    format_lobby_players_block,
)
from maps import map_display_name
from matchmaker import Matchmaker
from premier_veto_flow import PremierVetoPhase

QUEUE_STATUS_EMBED_TITLE = "CS2 Matchmaking Queue"
EMBED_FIELD_LIMIT = 1024


def _clamp_embed_text(text: str, limit: int = EMBED_FIELD_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."

if TYPE_CHECKING:
    from bot_app import MatchBot


def _ready_icon(ready: bool) -> str:
    return READY_EMOJI if ready else "⏳"


def _format_team_line(label: str, player_ids: list[int]) -> str:
    if not player_ids:
        return f"{embed_heading(label)}\n_empty_"
    members = "\n".join(embed_item(f"<@{player_id}>") for player_id in player_ids)
    return f"{embed_heading(label)}\n{members}"


def _pending_voter_mentions(lobby_ids: list[int], voted_ids: set[int]) -> str:
    pending = [f"<@{player_id}>" for player_id in lobby_ids if player_id not in voted_ids]
    if not pending:
        return "_everyone voted_"
    return ", ".join(pending)


def _mode_has_lobby_activity(
    matchmaker: Matchmaker,
    mode: MatchMode,
    default_map: str,
) -> bool:
    flow = matchmaker.get_captain_flow(mode, default_map)
    veto_flow = matchmaker.get_premier_veto_flow(mode, default_map)
    return flow.phase != CaptainPhase.NONE or veto_flow.phase != PremierVetoPhase.NONE


def _mode_lobby_status_block(
    matchmaker: Matchmaker,
    mode: MatchMode,
    default_map: str,
) -> str | None:
    flow = matchmaker.get_captain_flow(mode, default_map)
    veto_flow = matchmaker.get_premier_veto_flow(mode, default_map)
    ready_count = sum(
        1 for entry in matchmaker.get_mode_entries(mode, default_map) if entry.ready
    )

    if not _mode_has_lobby_activity(matchmaker, mode, default_map):
        return None

    lines = [embed_heading(f"{mode.label} on `{default_map}`", level=1)]

    if flow.phase != CaptainPhase.NONE:
        if flow.phase == CaptainPhase.DRAFTING and flow.team_alpha_captain_id is not None:
            lines.append(embed_heading("Captains"))
            lines.append(embed_item(f"Alpha <@{flow.team_alpha_captain_id}>"))
            lines.append(embed_item(f"Bravo <@{flow.team_bravo_captain_id}>"))
        flow_line = _captain_flow_line(flow, mode, ready_count)
        if flow_line:
            lines.append(flow_line)

    if veto_flow.phase != PremierVetoPhase.NONE:
        if veto_flow.alpha_captain_id is not None:
            lines.append(embed_heading("Captains"))
            lines.append(embed_item(f"Alpha <@{veto_flow.alpha_captain_id}>"))
            lines.append(embed_item(f"Bravo <@{veto_flow.bravo_captain_id}>"))
        veto_line = _premier_veto_flow_line(veto_flow, mode, ready_count)
        if veto_line:
            lines.append(veto_line)

    players_block = format_lobby_players_block(matchmaker, mode, default_map)
    if players_block:
        lines.append(players_block)

    return "\n".join(lines)


def build_consolidated_lobby_status(
    matchmaker: Matchmaker,
    default_map: str,
) -> str | None:
    blocks = [
        block
        for mode in MatchMode
        if (block := _mode_lobby_status_block(matchmaker, mode, default_map)) is not None
    ]
    if not blocks:
        return None
    return "\n\n".join(blocks)


def _captain_flow_line(
    flow: CaptainFlowState,
    mode: MatchMode,
    ready_count: int,
) -> str:
    if flow.phase == CaptainPhase.VOTING:
        alpha_done, bravo_done, total = flow.voting_progress()
        alpha_voters = set(flow.alpha_votes.keys())
        bravo_voters = set(flow.bravo_votes.keys())
        lines = [
            embed_heading("Captain vote", level=1),
            f"_{alpha_done}/{total} Alpha · {bravo_done}/{total} Bravo voted_",
            f"Still need Alpha vote: {_pending_voter_mentions(flow.lobby_ids, alpha_voters)}",
            f"Still need Bravo vote: {_pending_voter_mentions(flow.lobby_ids, bravo_voters)}",
            "React **1️⃣–🔟** for Team Alpha captain and **Ⓐ–Ⓙ** for Team Bravo captain on this message.",
        ]
        if flow.team_alpha_captain_id is None and alpha_done == total and bravo_done == total:
            lines.append("_Tallying captains…_")
        return "\n".join(lines)

    if flow.phase == CaptainPhase.DRAFTING:
        next_team = "Team Alpha" if flow.pick_turn == CaptainTeam.ALPHA else "Team Bravo"
        picker = flow.current_picker_id()
        picker_text = f"<@{picker}>" if picker is not None else "_unknown_"
        lines = [
            embed_heading("Player draft", level=1),
            embed_item(f"{picker_text} ({next_team}) picks next"),
            _format_team_line("Team Alpha", flow.team_alpha_ids),
            _format_team_line("Team Bravo", flow.team_bravo_ids),
            "React with **1️⃣–🔟** on this message to draft (see player list below).",
        ]
        return "\n".join(lines)

    if ready_count >= mode.total_players:
        return (
            "⚠️ Enough players ready — captain voting will start automatically. "
            "React on this message once captain voting opens."
        )

    return ""


def _premier_veto_flow_line(
    flow,
    mode: MatchMode,
    ready_count: int,
) -> str:
    if flow.phase == PremierVetoPhase.BANNING:
        turn_team = flow.team_label(flow.ban_turn_team())
        captain_id = flow.captain_for_team(flow.ban_turn_team())
        captain_text = f"<@{captain_id}>" if captain_id is not None else "_unknown_"
        ban_lines = [
            f"{flow.team_label(team)} banned **{map_display_name(map_id)}**"
            for team, map_id in flow.bans
        ]
        bans_left = flow.bans_remaining()
        lines = [
            embed_heading("Premier map veto", level=1),
            embed_item(f"{captain_text} ({turn_team}) bans next"),
            f"Bans left: **{bans_left}**",
        ]
        if ban_lines:
            lines.append(embed_heading("Banned maps"))
            lines.extend(embed_item(line) for line in ban_lines)
        lines.append(
            f"**{turn_team}** captain: react that map's fixed number below "
            "(numbers stay the same; banned maps lose their reaction)."
        )
        return "\n".join(lines)

    if flow.phase == PremierVetoPhase.SIDE_PICK:
        picker = flow.side_picker_team or CaptainTeam.ALPHA
        captain_id = flow.captain_for_team(picker)
        captain_text = f"<@{captain_id}>" if captain_id is not None else "_unknown_"
        chosen = map_display_name(flow.chosen_map or "")
        return (
            f"{embed_heading('Side pick', level=1)}\n"
            f"{embed_item(f'**{chosen}** (`{flow.chosen_map}`)')}\n"
            f"{embed_item(f'{captain_text} ({flow.team_label(picker)}) — {SIDE_CT_EMOJI} CT or {SIDE_T_EMOJI} T')}"
        )

    if ready_count >= mode.total_players:
        if mode == MatchMode.ONE_V_ONE:
            return (
                "⚠️ Enough players ready — **Premier map veto** will start automatically. "
                "Use numbered reactions, then **🛡️ CT** / **⚔️ T** for side pick."
            )
        return (
            "⚠️ Enough players ready — after captain draft, **Premier map veto** begins."
        )

    return ""


def build_queue_embed(
    matchmaker: Matchmaker,
    default_map: str,
    *,
    server_connect_field: str | None = None,
    active_match_lines: list[str] | None = None,
    ready_countdown_lines: dict[MatchMode, str] | None = None,
    lobby_activity_lines: dict[MatchMode, str] | None = None,
) -> discord.Embed:
    lobby_status = build_consolidated_lobby_status(matchmaker, default_map)
    embed_color = (
        discord.Color.gold() if lobby_status is not None else discord.Color.blurple()
    )

    embed = discord.Embed(
        title=QUEUE_STATUS_EMBED_TITLE,
        description=(
            "1. Click **Link Steam Account** on this channel or **#queue-status**; use **Unlink Steam** to remove a link\n"
            "2. Join a **Queue** voice channel below (leave the channel to leave the queue)\n"
            "3. React **✅** on this message when ready, or **❌** to unready\n"
            "4. **Captain vote / draft / map veto / side pick** — use the reactions on this message "
            "(numbers, Ⓐ–Ⓙ, 🛡️ CT, ⚔️ T)\n"
            "5. For **2v2 / 5v5**, vote captains and draft players, then map veto + side pick\n\n"
            "All lobby updates appear in **Lobby status** below, including every player in queue. "
            "React on this message when it is your turn.\n\n"
            "During matches, roster are placed in **CT** / **T** voice once, then anyone "
            "can use those channels and roster can join any other voice channel."
        ),
        color=embed_color,
    )

    if lobby_status is not None:
        embed.add_field(
            name="Lobby status",
            value=_clamp_embed_text(lobby_status),
            inline=False,
        )

    for mode in MatchMode:
        entries = matchmaker.get_mode_entries(mode, default_map)
        ready_count = sum(1 for entry in entries if entry.ready)
        voice_name = VOICE_CHANNEL_NAMES[mode]

        if entries:
            player_lines = []
            flow = matchmaker.get_captain_flow(mode, default_map)
            veto_flow = matchmaker.get_premier_veto_flow(mode, default_map)
            for entry in entries:
                suffix = ""
                if flow.in_lobby(entry.discord_id):
                    if flow.phase == CaptainPhase.VOTING:
                        vote_bits = []
                        if entry.discord_id in flow.alpha_votes:
                            vote_bits.append("A")
                        if entry.discord_id in flow.bravo_votes:
                            vote_bits.append("B")
                        if vote_bits:
                            suffix = f" · voted {'/'.join(vote_bits)}"
                    elif flow.phase == CaptainPhase.DRAFTING:
                        if entry.discord_id in flow.team_alpha_ids:
                            suffix = " · Team Alpha"
                        elif entry.discord_id in flow.team_bravo_ids:
                            suffix = " · Team Bravo"
                elif veto_flow.in_lobby(entry.discord_id):
                    if entry.discord_id in veto_flow.team_alpha_ids:
                        suffix = " · Team Alpha"
                    elif entry.discord_id in veto_flow.team_bravo_ids:
                        suffix = " · Team Bravo"
                elif flow.phase != CaptainPhase.NONE or veto_flow.phase != PremierVetoPhase.NONE:
                    suffix = " · joined after lobby locked"
                player_lines.append(
                    f"{_ready_icon(entry.ready)} <@{entry.discord_id}>{suffix}"
                )
            players_text = "\n".join(player_lines)
        else:
            players_text = "_No players in queue_"

        field_lines = [f"Voice: **{voice_name}**"]
        show_flow_in_field = lobby_status is None
        if lobby_status is not None and _mode_has_lobby_activity(matchmaker, mode, default_map):
            field_lines.append(
                f"_{ready_count}/{mode.total_players} ready — see **Lobby status** above for all players_"
            )
        else:
            field_lines.append(players_text if entries else "_No players in queue_")
        if show_flow_in_field and matchmaker.captains_required(mode):
            flow_line = _captain_flow_line(
                matchmaker.get_captain_flow(mode, default_map),
                mode,
                ready_count,
            )
            if flow_line:
                field_lines.append(flow_line)
        if show_flow_in_field:
            veto_line = _premier_veto_flow_line(
                matchmaker.get_premier_veto_flow(mode, default_map),
                mode,
                ready_count,
            )
            if veto_line:
                field_lines.append(veto_line)

        countdown = (ready_countdown_lines or {}).get(mode)
        if countdown:
            field_lines.append(countdown)

        lobby_activity = (lobby_activity_lines or {}).get(mode)
        if lobby_activity:
            field_lines.append(lobby_activity)

        embed.add_field(
            name=f"{mode.label} ({ready_count}/{mode.total_players} ready)",
            value=_clamp_embed_text("\n".join(field_lines)),
            inline=False,
        )

    if active_match_lines:
        embed.add_field(
            name="Active match",
            value=_clamp_embed_text("\n".join(active_match_lines)),
            inline=False,
        )

    if server_connect_field:
        embed.add_field(
            name="Join game server",
            value=_clamp_embed_text(server_connect_field),
            inline=False,
        )

    if matchmaker.active_matches:
        embed.set_footer(text=f"Active matches: {len(matchmaker.active_matches)}")

    return embed


class CaptainAlphaVoteSelect(discord.ui.Select):
    def __init__(
        self,
        bot: MatchBot,
        mode: MatchMode,
        map_name: str,
        candidates: list[tuple[int, str]],
    ) -> None:
        options = [
            discord.SelectOption(label=name[:100], value=str(discord_id))
            for discord_id, name in candidates[:25]
        ]
        super().__init__(
            placeholder="Vote for Team Alpha captain",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.bot = bot
        self.mode = mode
        self.map_name = map_name

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_captain_vote(
            interaction,
            self.mode,
            self.map_name,
            CaptainTeam.ALPHA,
            int(self.values[0]),
        )


class CaptainBravoVoteSelect(discord.ui.Select):
    def __init__(
        self,
        bot: MatchBot,
        mode: MatchMode,
        map_name: str,
        candidates: list[tuple[int, str]],
    ) -> None:
        options = [
            discord.SelectOption(label=name[:100], value=str(discord_id))
            for discord_id, name in candidates[:25]
        ]
        super().__init__(
            placeholder="Vote for Team Bravo captain",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.bot = bot
        self.mode = mode
        self.map_name = map_name

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_captain_vote(
            interaction,
            self.mode,
            self.map_name,
            CaptainTeam.BRAVO,
            int(self.values[0]),
        )


class CaptainVoteSelectView(discord.ui.View):
    def __init__(
        self,
        bot: MatchBot,
        mode: MatchMode,
        map_name: str,
        candidates: list[tuple[int, str]],
    ) -> None:
        super().__init__(timeout=120)
        self.add_item(CaptainAlphaVoteSelect(bot, mode, map_name, candidates))
        self.add_item(CaptainBravoVoteSelect(bot, mode, map_name, candidates))


class CaptainPickSelect(discord.ui.Select):
    def __init__(
        self,
        bot: MatchBot,
        mode: MatchMode,
        map_name: str,
        candidates: list[tuple[int, str]],
    ) -> None:
        options = [
            discord.SelectOption(label=name[:100], value=str(discord_id))
            for discord_id, name in candidates[:25]
        ]
        super().__init__(
            placeholder="Pick a player for your team",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.bot = bot
        self.mode = mode
        self.map_name = map_name

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_draft_pick(
            interaction,
            self.mode,
            self.map_name,
            int(self.values[0]),
        )


class PremierBanSelect(discord.ui.Select):
    def __init__(
        self,
        bot: MatchBot,
        mode: MatchMode,
        map_name: str,
        remaining_maps: list[str],
        *,
        placeholder: str = "Ban a map",
    ) -> None:
        options = [
            discord.SelectOption(
                label=f"{map_display_name(map_id)} ({map_id})",
                value=map_id,
            )
            for map_id in remaining_maps[:25]
        ]
        super().__init__(
            placeholder=placeholder[:100],
            min_values=1,
            max_values=1,
            options=options,
            row=1,
        )
        self.bot = bot
        self.mode = mode
        self.map_name = map_name

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_premier_ban(
            interaction,
            self.mode,
            self.map_name,
            self.values[0],
        )


def build_queue_status_view(
    bot: MatchBot,
    matchmaker: Matchmaker,
    default_map: str,
) -> discord.ui.View:
    return QueueControlView(bot)


class SidePickView(discord.ui.View):
    def __init__(self, bot: MatchBot, mode: MatchMode, map_name: str) -> None:
        super().__init__(timeout=120)
        self.bot = bot
        self.mode = mode
        self.map_name = map_name

    @discord.ui.button(label="Pick CT", style=discord.ButtonStyle.primary)
    async def pick_ct(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.bot.handle_premier_side(interaction, self.mode, self.map_name, "ct")

    @discord.ui.button(label="Pick T", style=discord.ButtonStyle.danger)
    async def pick_t(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.bot.handle_premier_side(interaction, self.mode, self.map_name, "t")


class CaptainPickSelectView(discord.ui.View):
    def __init__(
        self,
        bot: MatchBot,
        mode: MatchMode,
        map_name: str,
        candidates: list[tuple[int, str]],
    ) -> None:
        super().__init__(timeout=120)
        self.add_item(CaptainPickSelect(bot, mode, map_name, candidates))


class QueueControlView(discord.ui.View):
    def __init__(self, bot: MatchBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Link Steam Account",
        style=discord.ButtonStyle.secondary,
        custom_id="cs2match:steamlink",
    )
    async def link_steam_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.bot.handle_steam_link_request(interaction)

    @discord.ui.button(
        label="Unlink Steam",
        style=discord.ButtonStyle.danger,
        custom_id="cs2match:steamunlink",
    )
    async def unlink_steam_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.bot.handle_steam_unlink_request(interaction)
