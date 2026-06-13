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


def build_team_channel_overwrites(
    guild: discord.Guild,
) -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
    """Open team channels so anyone can listen in; roster enforcement is handled separately."""
    return {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            move_members=True,
            manage_channels=True,
        ),
    }


async def apply_team_channel_permissions(
    guild: discord.Guild,
    team1_channel: discord.VoiceChannel,
    team2_channel: discord.VoiceChannel,
) -> None:
    overwrites = build_team_channel_overwrites(guild)
    await team1_channel.edit(overwrites=overwrites, reason="Open Team Alpha for spectators")
    await team2_channel.edit(overwrites=overwrites, reason="Open Team Bravo for spectators")


async def create_match_voice_channels(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    match: ActiveMatch,
) -> MatchVoiceChannels:
    ct_name, t_name = side_channel_names(match)
    ct_channel = await guild.create_voice_channel(
        ct_name,
        category=category,
        overwrites=build_team_channel_overwrites(guild),
        user_limit=0,
    )
    t_channel = await guild.create_voice_channel(
        t_name,
        category=category,
        overwrites=build_team_channel_overwrites(guild),
        user_limit=0,
    )
    await apply_team_channel_permissions(guild, ct_channel, t_channel)
    return MatchVoiceChannels(
        match_id=match.match_id,
        team1_channel_id=ct_channel.id,
        team2_channel_id=t_channel.id,
    )


async def delete_match_voice_channels(
    guild: discord.Guild,
    team1_channel_id: int,
    team2_channel_id: int,
) -> None:
    for channel_id in (team1_channel_id, team2_channel_id):
        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(channel_id)
            except discord.HTTPException:
                logger.warning("Team voice channel %s not found for deletion", channel_id)
                continue
        if isinstance(channel, discord.VoiceChannel):
            try:
                await channel.delete(reason="CS2 match ended")
            except discord.HTTPException:
                logger.exception("Failed to delete team voice channel %s", channel_id)


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


async def enforce_player_team_voice(
    guild: discord.Guild,
    member: discord.Member,
    match: ActiveMatch,
    team1_channel_id: int,
    team2_channel_id: int,
) -> None:
    """Keep a rostered player in their assigned team voice channel during an active match."""
    expected_channel_id = team_channel_for_player(
        match,
        member.id,
        team1_channel_id,
        team2_channel_id,
    )
    if expected_channel_id is None:
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
    current_channel_id = (
        member.voice.channel.id if member.voice is not None and member.voice.channel is not None else None
    )
    if current_channel_id == expected_channel_id:
        return

    side_label = "CT" if expected_channel_id == team1_channel_id else "T"
    match_channel_ids = {team1_channel_id, team2_channel_id}
    wrong_side_channel = (
        current_channel_id in match_channel_ids
        and current_channel_id != expected_channel_id
    )
    in_queue_or_other_voice = (
        current_channel_id is not None
        and current_channel_id not in match_channel_ids
    )
    if wrong_side_channel or in_queue_or_other_voice:
        await _move_member_to_channel(member, expected_channel, team_label=side_label)


async def restore_players_to_original_channels(
    guild: discord.Guild,
    original_channels: dict[int, int],
    team1_channel_id: int,
    team2_channel_id: int,
) -> None:
    team_channel_ids = {team1_channel_id, team2_channel_id}

    for discord_id, original_channel_id in original_channels.items():
        member = guild.get_member(discord_id)
        if member is None:
            continue
        if member.voice is None or member.voice.channel is None:
            continue
        if member.voice.channel.id not in team_channel_ids:
            continue

        original_channel = guild.get_channel(original_channel_id)
        if not isinstance(original_channel, discord.VoiceChannel):
            continue
        if original_channel.id in team_channel_ids:
            continue

        try:
            await member.move_to(original_channel)
        except discord.HTTPException:
            pass
