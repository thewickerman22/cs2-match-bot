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


def build_match_channel_overwrites(
    guild: discord.Guild,
) -> dict[discord.Role | discord.Member | discord.Object, discord.PermissionOverwrite]:
    """Allow all server members to connect to match CT/T voice channels."""
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


async def apply_match_channel_permissions(
    guild: discord.Guild,
    match: ActiveMatch,
    ct_channel: discord.VoiceChannel,
    t_channel: discord.VoiceChannel,
) -> None:
    overwrites = build_match_channel_overwrites(guild)
    await ct_channel.edit(
        overwrites=overwrites,
        reason="Open match voice channels",
    )
    await t_channel.edit(
        overwrites=overwrites,
        reason="Open match voice channels",
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


async def delete_match_voice_channels_for_match(
    guild: discord.Guild,
    match_id: str,
    team1_channel_id: int | None = None,
    team2_channel_id: int | None = None,
) -> bool:
    """Delete CT/T voice channels for a match, resolving by id or channel name."""
    if team1_channel_id is None or team2_channel_id is None:
        discovered = await find_match_voice_channels_by_name(guild, match_id)
        if discovered is None:
            return False
        team1_channel_id, team2_channel_id = discovered
    return await delete_match_voice_channels(
        guild,
        team1_channel_id,
        team2_channel_id,
    )


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


async def move_roster_to_initial_team_channels(
    guild: discord.Guild,
    match: ActiveMatch,
    team1_channel_id: int,
    team2_channel_id: int,
) -> None:
    """One-time initial move: place roster in assigned CT/T channels when a match starts."""
    team1_channel = guild.get_channel(team1_channel_id)
    team2_channel = guild.get_channel(team2_channel_id)
    if not isinstance(team1_channel, discord.VoiceChannel):
        return
    if not isinstance(team2_channel, discord.VoiceChannel):
        return

    ct_channel_id = team1_channel_id
    t_channel_id = team2_channel_id
    ct_channel = team1_channel
    t_channel = team2_channel

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
        await _move_member_to_channel(member, target_channel, team_label=side_label)


# Backwards-compatible alias used by bot_app.
move_players_to_team_channels = move_roster_to_initial_team_channels


async def move_match_players_to_end_queue(
    guild: discord.Guild,
    roster_ids: set[int],
    end_queue_channel_id: int,
    *,
    team1_channel_id: int | None = None,
    team2_channel_id: int | None = None,
) -> None:
    """Move match roster (and anyone left in team channels) into End Queue."""
    end_queue = await _resolve_voice_channel(guild, end_queue_channel_id)
    if end_queue is None:
        logger.warning("End Queue channel %s not found", end_queue_channel_id)
        return

    moved_ids: set[int] = set()

    for discord_id in roster_ids:
        member = await _resolve_member(guild, discord_id)
        if member is None:
            continue
        if member.voice is None or member.voice.channel is None:
            continue
        if member.voice.channel.id == end_queue_channel_id:
            continue
        if await _move_member_to_channel(member, end_queue, team_label="End Queue"):
            moved_ids.add(member.id)

    team_channel_ids: set[int] = set()
    for channel_id in (team1_channel_id, team2_channel_id):
        if channel_id is None:
            continue
        team_channel_ids.add(channel_id)
        channel = await _resolve_voice_channel(guild, channel_id)
        if channel is None:
            continue
        for member in list(channel.members):
            if member.bot or member.id in moved_ids:
                continue
            if member.voice is None or member.voice.channel is None:
                continue
            if await _move_member_to_channel(member, end_queue, team_label="End Queue"):
                moved_ids.add(member.id)

    if moved_ids:
        logger.info(
            "Moved %s player(s) to End Queue (%s)",
            len(moved_ids),
            end_queue_channel_id,
        )
