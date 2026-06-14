from __future__ import annotations

from dataclasses import dataclass

import discord

from config import MatchMode

CATEGORY_NAME = "CS2 Matchmaking"
STATUS_CHANNEL_NAME = "queue-status"
RESULTS_CHANNEL_NAME = "match-results"
ELO_CHANNEL_NAME = "elo-leaderboard"
VOICE_CHANNEL_NAMES = {
    MatchMode.ONE_V_ONE: "Queue » 1v1",
    MatchMode.TWO_V_TWO: "Queue » 2v2",
    MatchMode.FIVE_V_FIVE: "Queue » 5v5",
}
END_QUEUE_CHANNEL_NAME = "End Queue"


@dataclass(frozen=True)
class GuildSetup:
    guild_id: int
    category_id: int
    status_channel_id: int
    status_message_id: int | None
    results_channel_id: int
    elo_channel_id: int
    elo_message_id: int | None
    voice_channels: dict[MatchMode, int]
    end_queue_channel_id: int = 0

    def mode_for_voice_channel(self, channel_id: int) -> MatchMode | None:
        for mode, voice_id in self.voice_channels.items():
            if voice_id == channel_id:
                return mode
        return None

    def is_queue_voice_channel(self, channel_id: int) -> bool:
        return channel_id in self.voice_channels.values()


def mode_for_voice_channel_name(name: str) -> MatchMode | None:
    for mode, channel_name in VOICE_CHANNEL_NAMES.items():
        if channel_name == name:
            return mode
    return None


def resolve_queue_mode(
    setup: GuildSetup | None,
    channel: discord.VoiceChannel | None,
) -> MatchMode | None:
    if channel is None:
        return None
    if setup is not None:
        mode = setup.mode_for_voice_channel(channel.id)
        if mode is not None:
            return mode
    return mode_for_voice_channel_name(channel.name)


async def ensure_guild_setup(
    guild: discord.Guild,
    existing: GuildSetup | None = None,
) -> GuildSetup:
    category = discord.utils.get(guild.categories, name=CATEGORY_NAME)
    if category is None:
        category = await guild.create_category(CATEGORY_NAME)

    status_channel = _resolve_text_channel(
        guild,
        category,
        STATUS_CHANNEL_NAME,
        existing.status_channel_id if existing else None,
    )
    if status_channel is None:
        status_channel = await guild.create_text_channel(
            STATUS_CHANNEL_NAME,
            category=category,
            topic="Queue status and ready controls for CS2 matchmaking.",
        )

    results_channel = _resolve_text_channel(
        guild,
        category,
        RESULTS_CHANNEL_NAME,
        existing.results_channel_id if existing else None,
    )
    if results_channel is None:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=False,
                read_message_history=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                embed_links=True,
                read_message_history=True,
            ),
        }
        results_channel = await guild.create_text_channel(
            RESULTS_CHANNEL_NAME,
            category=category,
            topic="Recent CS2 match results posted automatically after each match.",
            overwrites=overwrites,
        )

    elo_channel = _resolve_text_channel(
        guild,
        category,
        ELO_CHANNEL_NAME,
        existing.elo_channel_id if existing and existing.elo_channel_id else None,
    )
    if elo_channel is None:
        elo_channel = await guild.create_text_channel(
            ELO_CHANNEL_NAME,
            category=category,
            topic="Live ELO leaderboard — updated after each ranked match. Resets every 3 months.",
            overwrites=overwrites,
        )

    voice_channels: dict[MatchMode, int] = {}
    for mode, channel_name in VOICE_CHANNEL_NAMES.items():
        existing_id = existing.voice_channels.get(mode) if existing else None
        voice_channel = _resolve_voice_channel(guild, category, channel_name, existing_id)
        if voice_channel is None:
            voice_channel = await guild.create_voice_channel(
                channel_name,
                category=category,
                user_limit=0,
            )
        voice_channels[mode] = voice_channel.id

    end_queue_channel = _resolve_voice_channel(
        guild,
        category,
        END_QUEUE_CHANNEL_NAME,
        existing.end_queue_channel_id if existing else None,
    )
    if end_queue_channel is None:
        end_queue_channel = await guild.create_voice_channel(
            END_QUEUE_CHANNEL_NAME,
            category=category,
            user_limit=0,
        )

    return GuildSetup(
        guild_id=guild.id,
        category_id=category.id,
        status_channel_id=status_channel.id,
        status_message_id=existing.status_message_id if existing else None,
        results_channel_id=results_channel.id,
        elo_channel_id=elo_channel.id,
        elo_message_id=existing.elo_message_id if existing else None,
        voice_channels=voice_channels,
        end_queue_channel_id=end_queue_channel.id,
    )


def _resolve_text_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    name: str,
    channel_id: int | None,
) -> discord.TextChannel | None:
    if channel_id is not None:
        channel = guild.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel) and channel.category_id == category.id:
            return channel

    channel = discord.utils.get(guild.text_channels, name=name)
    if channel is not None and channel.category_id == category.id:
        return channel
    return None


def _resolve_voice_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    name: str,
    channel_id: int | None,
) -> discord.VoiceChannel | None:
    if channel_id is not None:
        channel = guild.get_channel(channel_id)
        if isinstance(channel, discord.VoiceChannel) and channel.category_id == category.id:
            return channel

    channel = discord.utils.get(guild.voice_channels, name=name)
    if channel is not None and channel.category_id == category.id:
        return channel
    return None
