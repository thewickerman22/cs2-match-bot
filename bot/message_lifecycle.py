"""Auto-delete transient bot messages; retain recent match results."""

from __future__ import annotations

import logging

import discord

logger = logging.getLogger(__name__)


async def send_transient(
    channel: discord.abc.Messageable,
    content: str | None = None,
    *,
    embed: discord.Embed | None = None,
    view: discord.ui.View | None = None,
    delete_after: float,
) -> discord.Message:
    """Send a guild message that the bot deletes after a short delay."""
    return await channel.send(
        content,
        embed=embed,
        view=view,
        delete_after=delete_after,
    )


async def register_match_result_message(
    storage,
    guild: discord.Guild,
    results_channel: discord.TextChannel,
    message_id: int,
    match_id: str,
    *,
    retain_count: int,
    protected_message_ids: set[int],
) -> None:
    """Keep the newest `retain_count` result messages; delete older bot results."""
    await storage.add_guild_result_message(guild.id, message_id, match_id)
    stale = await storage.list_guild_result_messages(guild.id, offset=retain_count)
    for old_message_id, old_match_id in stale:
        if old_message_id in protected_message_ids:
            continue
        try:
            old_message = await results_channel.fetch_message(old_message_id)
            if old_message.author.id == guild.me.id:
                await old_message.delete()
        except discord.NotFound:
            pass
        except discord.HTTPException:
            logger.exception(
                "Failed to delete old result message %s (match %s)",
                old_message_id,
                old_match_id,
            )
        await storage.remove_guild_result_message(guild.id, old_message_id)
