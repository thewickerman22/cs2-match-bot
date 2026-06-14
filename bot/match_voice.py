from __future__ import annotations

from dataclasses import dataclass

import logging

import discord

from matchzy import ActiveMatch
from match_sides import player_side_channel_id, side_channel_names

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MatchVoiceChannels:
    match_id: str
    team1_channel_id: int
    team2_channel_id: int


def team_channel_names(match: ActiveMatch) -> tuple[str, str]:
    return side_channel_names(match)


def team_channel_for_player(
    match: ActiveMatch,
    discord_id: int,
    team1_channel_id: int,
    team2_channel_id: int,
) -> int | None:
    return player_side_channel_id(match, discord_id, team1_channel_id, team2_channel_id)


async def _resolve_member(guild: discord.Guild, discord_id: int) -> discord.Member | None:
    member = guild.get_member(discord_id)
    if member is not None:
        return member
    try:
        return await guild.fetch_member(discord_id)
    except discord.HTTPException:
        return None


def match_roster_ids(match: ActiveMatch) -> set[int]:
    return {player.discord_id for player in match.team1 + match.team2}


def build_side_channel_overwrites(
    guild: discord.Guild,
    match: ActiveMatch,
    side_channel_id: int,
    ct_channel_id: int,
    t_channel_id: int,
) -> dict[discord.Role | discord.Member | discord.Object, discord.PermissionOverwrite]:
    """Only roster players assigned to this side may connect."""
    overwrites: dict[
        discord.Role | discord.Member | discord.Object,
        discord.PermissionOverwrite,
    ] = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=True,
            connect=False,
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            move_members=True,
            manage_channels=True,
        ),
    }

    for player in match.team1 + match.team2:
        allowed_channel_id = player_side_channel_id(
            match,
            player.discord_id,
            ct_channel_id,
            t_channel_id,
        )
        if allowed_channel_id != side_channel_id:
            continue
        overwrites[discord.Object(id=player.discord_id)] = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
        )

    return overwrites


async def apply_match_channel_permissions(
    guild: discord.Guild,
    match: ActiveMatch,
    ct_channel: discord.VoiceChannel,
    t_channel: discord.VoiceChannel,
) -> None:
    await ct_channel.edit(
        overwrites=build_side_channel_overwrites(
            guild,
            match,
            ct_channel.id,
            ct_channel.id,
            t_channel.id,
        ),
        reason="Restrict CT voice to match roster",
    )
    await t_channel.edit(
        overwrites=build_side_channel_overwrites(
            guild,
            match,
            t_channel.id,
            ct_channel.id,
            t_channel.id,
        ),
        reason="Restrict T voice to match roster",
    )


async def create_match_voice_channels(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    match: ActiveMatch,
) -> MatchVoiceChannels:
    ct_name, t_name = side_channel_names(match)
    ct_channel = await guild.create_voice_channel(
        ct_name,
        category=category,
        user_limit=0,
    )
    t_channel = await guild.create_voice_channel(
        t_name,
        category=category,
        user_limit=0,
    )
    await apply_match_channel_permissions(guild, match, ct_channel, t_channel)
    return MatchVoiceChannels(
        match_id=match.match_id,
        team1_channel_id=ct_channel.id,
        team2_channel_id=t_channel.id,
    )


def match_voice_channel_prefix(match_id: str) -> str:
    return f"Match {match_id} »"


async def _resolve_voice_channel(
    guild: discord.Guild,
    channel_id: int,
) -> discord.VoiceChannel | None:
    channel = guild.get_channel(channel_id)
    if isinstance(channel, discord.VoiceChannel):
        return channel
    try:
        fetched = await guild.fetch_channel(channel_id)
    except discord.HTTPException:
        return None
    if isinstance(fetched, discord.VoiceChannel):
        return fetched
    return None


def _side_from_channel_name(name: str) -> str | None:
    if name.endswith("(CT)"):
        return "ct"
    if name.endswith("(T)"):
        return "t"
    return None


async def find_match_voice_channels_by_name(
    guild: discord.Guild,
    match_id: str,
) -> tuple[int, int] | None:
    prefix = match_voice_channel_prefix(match_id)
    matches = [
        channel
        for channel in guild.voice_channels
        if channel.name.startswith(prefix)
    ]
    if len(matches) < 2:
        if len(matches) == 1:
            logger.warning(
                "Only one team voice channel found for match %s (%s)",
                match_id,
                matches[0].name,
            )
        return None

    ct_channel_id: int | None = None
    t_channel_id: int | None = None
    for channel in matches:
        side = _side_from_channel_name(channel.name)
        if side == "ct":
            ct_channel_id = channel.id
        elif side == "t":
            t_channel_id = channel.id

    if ct_channel_id is not None and t_channel_id is not None:
        return ct_channel_id, t_channel_id

    logger.warning(
        "Could not identify CT/T voice channels for match %s from names: %s",
        match_id,
        ", ".join(channel.name for channel in matches),
    )
    matches.sort(key=lambda channel: channel.name)
    return matches[0].id, matches[1].id


async def delete_match_voice_channels(
    guild: discord.Guild,
    team1_channel_id: int,
    team2_channel_id: int,
) -> bool:
    deleted_all = True
    seen: set[int] = set()

    for channel_id in (team1_channel_id, team2_channel_id):
        if channel_id in seen:
            continue
        seen.add(channel_id)

        channel = await _resolve_voice_channel(guild, channel_id)
        if channel is None:
            logger.warning("Team voice channel %s not found for deletion", channel_id)
            continue

        for member in list(channel.members):
            if member.id == guild.me.id:
                continue
            try:
                await member.move_to(None, reason="CS2 match ended")
            except discord.HTTPException:
                logger.warning(
                    "Could not disconnect %s from team voice channel %s before deletion",
                    member.id,
                    channel_id,
                )

        try:
            await channel.delete(reason="CS2 match ended")
            logger.info("Deleted team voice channel %s (%s)", channel_id, channel.name)
        except discord.Forbidden:
            logger.exception(
                "Missing permission to delete team voice channel %s — "
                "move the bot role above other roles and grant Manage Channels",
                channel_id,
            )
            deleted_all = False
        except discord.HTTPException:
            logger.exception("Failed to delete team voice channel %s", channel_id)
            deleted_all = False

    return deleted_all


async def _move_member_to_channel(
    member: discord.Member,
    channel: discord.VoiceChannel,
    *,
    team_label: str,
) -> bool:
    try:
        await member.move_to(channel)
        return True
    except discord.HTTPException:
        logger.warning(
            "Could not move %s (%s) to %s — check bot role is above player roles and has Move Members",
            member.display_name,
            member.id,
            team_label,
        )
        return False


async def move_players_to_team_channels(
    guild: discord.Guild,
    match: ActiveMatch,
    team1_channel_id: int,
    team2_channel_id: int,
) -> dict[int, int]:
    """Move players to their assigned team channels and return previous voice channel IDs."""
    team1_channel = guild.get_channel(team1_channel_id)
    team2_channel = guild.get_channel(team2_channel_id)
    if not isinstance(team1_channel, discord.VoiceChannel):
        return {}
    if not isinstance(team2_channel, discord.VoiceChannel):
        return {}

    original_channels: dict[int, int] = {}

    ct_channel_id = team1_channel_id
    t_channel_id = team2_channel_id
    ct_channel = guild.get_channel(ct_channel_id)
    t_channel = guild.get_channel(t_channel_id)
    if not isinstance(ct_channel, discord.VoiceChannel):
        return {}
    if not isinstance(t_channel, discord.VoiceChannel):
        return {}

    for player in match.team1 + match.team2:
        member = await _resolve_member(guild, player.discord_id)
        if member is None:
            logger.warning("Roster player %s not found in guild", player.discord_id)
            continue

        target_channel_id = player_side_channel_id(
            match,
            player.discord_id,
            ct_channel_id,
            t_channel_id,
        )
        if target_channel_id is None:
            continue

        target_channel = ct_channel if target_channel_id == ct_channel_id else t_channel
        side_label = "CT" if target_channel_id == ct_channel_id else "T"
        if member.voice is not None and member.voice.channel is not None:
            if member.voice.channel.id != target_channel_id:
                original_channels[player.discord_id] = member.voice.channel.id
        await _move_member_to_channel(member, target_channel, team_label=side_label)

    return original_channels


async def enforce_match_voice_access(
    guild: discord.Guild,
    member: discord.Member,
    match: ActiveMatch,
    team1_channel_id: int,
    team2_channel_id: int,
) -> None:
    """Keep match voice channels limited to the match roster on their assigned side."""
    match_channel_ids = {team1_channel_id, team2_channel_id}
    current_channel_id = (
        member.voice.channel.id
        if member.voice is not None and member.voice.channel is not None
        else None
    )
    if current_channel_id not in match_channel_ids:
        return

    if member.id not in match_roster_ids(match):
        try:
            await member.move_to(None, reason="Not a player in this match")
        except discord.HTTPException:
            logger.warning(
                "Could not remove %s (%s) from match voice channel %s",
                member.display_name,
                member.id,
                current_channel_id,
            )
        return

    expected_channel_id = team_channel_for_player(
        match,
        member.id,
        team1_channel_id,
        team2_channel_id,
    )
    if expected_channel_id is None or current_channel_id == expected_channel_id:
        return

    ct_channel = guild.get_channel(team1_channel_id)
    t_channel = guild.get_channel(team2_channel_id)
    if not isinstance(ct_channel, discord.VoiceChannel):
        return
    if not isinstance(t_channel, discord.VoiceChannel):
        return

    expected_channel = (
        ct_channel if expected_channel_id == team1_channel_id else t_channel
    )
    side_label = "CT" if expected_channel_id == team1_channel_id else "T"
    await _move_member_to_channel(member, expected_channel, team_label=side_label)


async def enforce_player_team_voice(
    guild: discord.Guild,
    member: discord.Member,
    match: ActiveMatch,
    team1_channel_id: int,
    team2_channel_id: int,
) -> None:
    await enforce_match_voice_access(
        guild,
        member,
        match,
        team1_channel_id,
        team2_channel_id,
    )


async def move_match_players_to_end_queue(
    guild: discord.Guild,
    roster_ids: set[int],
    team1_channel_id: int,
    team2_channel_id: int,
    end_queue_channel_id: int,
) -> None:
    """Move match roster players from team voice channels into End Queue."""
    end_queue = await _resolve_voice_channel(guild, end_queue_channel_id)
    if end_queue is None:
        logger.warning("End Queue channel %s not found", end_queue_channel_id)
        return

    team_channel_ids = {team1_channel_id, team2_channel_id}
    moved = 0

    for discord_id in roster_ids:
        member = await _resolve_member(guild, discord_id)
        if member is None:
            continue
        if member.voice is None or member.voice.channel is None:
            continue
        if member.voice.channel.id not in team_channel_ids:
            continue
        if await _move_member_to_channel(member, end_queue, team_label="End Queue"):
            moved += 1

    if moved:
        logger.info(
            "Moved %s match player(s) to End Queue (%s)",
            moved,
            end_queue_channel_id,
        )
