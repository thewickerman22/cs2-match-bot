from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from captain_flow import CaptainFlowState, CaptainPhase, CaptainTeam
from config import MatchMode
from guild_setup import VOICE_CHANNEL_NAMES
from maps import map_display_name
from premier_veto_flow import PremierVetoPhase
from matchmaker import Matchmaker

if TYPE_CHECKING:
    from bot_app import MatchBot


READY_EMOJI = "✅"
UNREADY_EMOJI = "❌"


def _ready_icon(ready: bool) -> str:
    return READY_EMOJI if ready else "⏳"


def _format_team_line(label: str, player_ids: list[int]) -> str:
    if not player_ids:
        return f"**{label}:** _empty_"
    members = " · ".join(f"<@{player_id}>" for player_id in player_ids)
    return f"**{label}:** {members}"


def _pending_voter_mentions(lobby_ids: list[int], voted_ids: set[int]) -> str:
    pending = [f"<@{player_id}>" for player_id in lobby_ids if player_id not in voted_ids]
    if not pending:
        return "_everyone voted_"
    return ", ".join(pending)


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
            f"🗳️ **Captain vote** ({alpha_done}/{total} Alpha · {bravo_done}/{total} Bravo)",
            f"Still need Alpha vote: {_pending_voter_mentions(flow.lobby_ids, alpha_voters)}",
            f"Still need Bravo vote: {_pending_voter_mentions(flow.lobby_ids, bravo_voters)}",
            "Lobby players: use **Vote Captains** in this channel.",
        ]
        if flow.team_alpha_captain_id is None and alpha_done == total and bravo_done == total:
            lines.append("_Tallying captains…_")
        return "\n".join(lines)

    if flow.phase == CaptainPhase.DRAFTING:
        next_team = "Team Alpha" if flow.pick_turn == CaptainTeam.ALPHA else "Team Bravo"
        picker = flow.current_picker_id()
        picker_text = f"<@{picker}>" if picker is not None else "_unknown_"
        lines = [
            f"🎯 **Player draft** — {picker_text} ({next_team}) picks next.",
            _format_team_line("Team Alpha", flow.team_alpha_ids),
            _format_team_line("Team Bravo", flow.team_bravo_ids),
            f"Available: {', '.join(f'<@{player_id}>' for player_id in flow.available_pick_ids) or '_none_'}",
        ]
        return "\n".join(lines)

    if ready_count >= mode.total_players:
        return (
            "⚠️ Enough players ready — captain voting will start automatically. "
            "Use **Vote Captains** once the lobby opens."
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
            f"- {flow.team_label(team)} banned **{map_display_name(map_id)}**"
            for team, map_id in flow.bans
        ]
        remaining = ", ".join(map_display_name(map_id) for map_id in sorted(flow.remaining_maps))
        bans_left = flow.bans_remaining()
        lines = [
            f"🗺️ **Premier map veto** — {captain_text} ({turn_team}) bans next",
            f"Bans left: **{bans_left}** · Remaining maps: {remaining or '_none_'}",
        ]
        if ban_lines:
            lines.append("\n".join(ban_lines))
        lines.append("Captains: use **Ban Map** in this channel.")
        return "\n".join(lines)

    if flow.phase == PremierVetoPhase.SIDE_PICK:
        picker = flow.side_picker_team or CaptainTeam.ALPHA
        captain_id = flow.captain_for_team(picker)
        captain_text = f"<@{captain_id}>" if captain_id is not None else "_unknown_"
        chosen = map_display_name(flow.chosen_map or "")
        return (
            f"🎯 **Side pick** on **{chosen}** (`{flow.chosen_map}`)\n"
            f"{captain_text} ({flow.team_label(picker)}) picks **CT** or **T** with **Pick Side**."
        )

    if ready_count >= mode.total_players:
        if mode == MatchMode.ONE_V_ONE:
            return (
                "⚠️ Enough players ready — **Premier map veto** will start automatically. "
                "Captains use **Ban Map**, then **Pick Side**."
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
    embed = discord.Embed(
        title="CS2 Matchmaking Queue",
        description=(
            "1. Click **Link Steam Account** on this channel or **#queue-status**; use **Unlink Steam** to remove a link\n"
            "2. Join a **Queue** voice channel below (leave the channel to leave the queue)\n"
            "3. React **✅** on this message when ready, or **❌** to unready\n"
            "4. **Premier map veto**: captains alternate **Ban Map** on Active Duty maps, then **Pick Side** (CT/T)\n"
            "5. For **2v2 / 5v5**, vote captains and **Pick Player**, then map veto + side pick\n\n"
            "During matches, roster are placed in **CT** / **T** voice once, then anyone "
            "can use those channels and roster can join any other voice channel."
        ),
        color=discord.Color.blurple(),
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

        field_lines = [
            f"Voice: **{voice_name}**",
            players_text,
        ]
        if matchmaker.captains_required(mode):
            flow_line = _captain_flow_line(
                matchmaker.get_captain_flow(mode, default_map),
                mode,
                ready_count,
            )
            if flow_line:
                field_lines.append(flow_line)
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
            value="\n".join(field_lines),
            inline=False,
        )

    if active_match_lines:
        embed.add_field(
            name="Active match",
            value="\n".join(active_match_lines),
            inline=False,
        )

    if server_connect_field:
        embed.add_field(
            name="Join game server",
            value=server_connect_field,
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
    ) -> None:
        options = [
            discord.SelectOption(
                label=f"{map_display_name(map_id)} ({map_id})",
                value=map_id,
            )
            for map_id in remaining_maps[:25]
        ]
        super().__init__(
            placeholder="Ban a map",
            min_values=1,
            max_values=1,
            options=options,
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


class PremierBanSelectView(discord.ui.View):
    def __init__(
        self,
        bot: MatchBot,
        mode: MatchMode,
        map_name: str,
        remaining_maps: list[str],
    ) -> None:
        super().__init__(timeout=120)
        self.add_item(PremierBanSelect(bot, mode, map_name, remaining_maps))


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

    @discord.ui.button(
        label="Ban Map",
        style=discord.ButtonStyle.primary,
        custom_id="cs2match:mapvote",
    )
    async def premier_ban_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.bot.handle_open_premier_ban(interaction)

    @discord.ui.button(
        label="Pick Side",
        style=discord.ButtonStyle.success,
        custom_id="cs2match:picksides",
    )
    async def pick_side_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.bot.handle_open_side_pick(interaction)

    @discord.ui.button(
        label="Vote Captains",
        style=discord.ButtonStyle.primary,
        custom_id="cs2match:vote",
    )
    async def vote_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.bot.handle_open_captain_vote(interaction)

    @discord.ui.button(
        label="Pick Player",
        style=discord.ButtonStyle.primary,
        custom_id="cs2match:pick",
    )
    async def pick_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.bot.handle_open_draft_pick(interaction)
