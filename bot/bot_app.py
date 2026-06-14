from __future__ import annotations

import asyncio
import logging
import sqlite3

import discord
from discord.ext import commands

from config import MatchMode, ServerProvider, Settings
from captain_flow import CaptainPhase, CaptainTeam
from premier_veto_flow import PremierVetoPhase
from elo_service import EloService
from guild_setup import GuildSetup, STATUS_CHANNEL_NAME, ELO_CHANNEL_NAME, ensure_guild_setup, resolve_queue_mode
from elo_leaderboard import build_leaderboard_embed
from match_voice import (
    create_match_voice_channels,
    delete_match_voice_channels,
    enforce_player_team_voice,
    find_match_voice_channels_by_name,
    move_match_players_to_end_queue,
    move_players_to_team_channels,
)
from matchzy_events import FINISH_EVENTS, LIVE_UPDATE_EVENTS, extract_match_id, normalize_event_name
from live_match import LiveMatchSnapshot, build_live_match_embed
from match_results import build_match_result_embed
from matchmaker import Matchmaker
from matchzy import ActiveMatch, MatchZyService, serialize_match_config
from queue_ui import (
    CaptainPickSelectView,
    CaptainVoteSelectView,
    PremierBanSelectView,
    SidePickView,
    QueueControlView,
    READY_EMOJI,
    UNREADY_EMOJI,
    build_queue_embed,
)
from steam_link_ui import SteamLinkDmView, SteamLinkModal, SteamUnlinkConfirmView
from server_connect import ServerConnectResolver
from storage import Storage
from utils import build_connect_info, build_server_connect_field, format_team, normalize_steam_id

logger = logging.getLogger(__name__)

COMMAND_PREFIX = "q!"


def parse_match_mode(value: str) -> MatchMode:
    normalized = value.lower().strip()
    aliases = {
        "1v1": MatchMode.ONE_V_ONE,
        "1": MatchMode.ONE_V_ONE,
        "2v2": MatchMode.TWO_V_TWO,
        "2": MatchMode.TWO_V_TWO,
        "wingman": MatchMode.TWO_V_TWO,
        "5v5": MatchMode.FIVE_V_FIVE,
        "5": MatchMode.FIVE_V_FIVE,
    }
    if normalized in aliases:
        return aliases[normalized]
    try:
        return MatchMode(normalized)
    except ValueError as exc:
        raise commands.BadArgument(
            f"Unknown mode `{value}`. Use `1v1`, `2v2`, or `5v5`."
        ) from exc


def parse_captain_team(value: str) -> str:
    normalized = value.lower().strip()
    if normalized in {"alpha", "a", "team1", "teamalpha", "1"}:
        return CaptainTeam.ALPHA
    if normalized in {"bravo", "b", "team2", "teambravo", "2"}:
        return CaptainTeam.BRAVO
    raise commands.BadArgument("Team must be `alpha` or `bravo`.")


class MatchBot(commands.Bot):
    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        matchmaker: Matchmaker,
        matchzy: MatchZyService,
        elo_service: EloService,
        connect_resolver: ServerConnectResolver,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        intents.reactions = True
        super().__init__(
            command_prefix=COMMAND_PREFIX,
            case_insensitive=True,
            intents=intents,
            help_command=None,
        )
        self.settings = settings
        self.storage = storage
        self.matchmaker = matchmaker
        self.matchzy = matchzy
        self.elo_service = elo_service
        self.connect_resolver = connect_resolver
        self.guild_setups: dict[int, GuildSetup] = {}
        self._reaction_sync_user_ids: set[int] = set()
        self._queue_ready_timer_tasks: dict[tuple[MatchMode, str], asyncio.Task] = {}
        self._match_status_messages: dict[str, int] = {}
        self._match_voice_channels: dict[str, tuple[int, int]] = {}
        self._match_finish_fallback_tasks: dict[str, asyncio.Task] = {}
        self._live_match_snapshots: dict[str, LiveMatchSnapshot] = {}

    async def _purge_discord_commands(self, guild_id: int | None) -> None:
        app_id = self.application_id
        if app_id is None:
            return
        try:
            await self.http.bulk_upsert_global_commands(app_id, [])
            logger.info("Purged all global application commands from Discord")
            if guild_id is not None:
                await self.http.bulk_upsert_guild_commands(app_id, guild_id, [])
                logger.info(
                    "Purged all guild application commands from Discord for guild %s",
                    guild_id,
                )
        except discord.Forbidden:
            logger.warning("Could not purge old slash commands from Discord")
        except Exception:
            logger.exception("Failed to purge application commands")

    async def setup_hook(self) -> None:
        self.add_view(QueueControlView(self))
        self.add_view(SteamLinkDmView(self))

    async def on_command_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"Missing argument: `{error.param.name}`. Use `{COMMAND_PREFIX}help`.")
            return
        if isinstance(error, (commands.BadArgument, commands.CheckFailure, commands.NoPrivateMessage)):
            await ctx.send(str(error))
            return
        logger.exception("Command %s failed for user %s", ctx.command, ctx.author.id)
        await ctx.send(f"Command failed: {error}")

    async def on_ready(self) -> None:
        logger.info("Logged in as %s", self.user)
        await self._purge_discord_commands(self.settings.discord_guild_id)

        if self.settings.discord_guild_id is None:
            logger.warning("DISCORD_GUILD_ID is not set; auto channel setup is disabled.")
            return

        guild = self.get_guild(self.settings.discord_guild_id)
        if guild is None:
            logger.warning(
                "Configured guild %s not found in cache. "
                "If the bot is not in that server, fix DISCORD_GUILD_ID and re-invite the bot.",
                self.settings.discord_guild_id,
            )
            return

        await self.ensure_guild_channels(guild)
        season, season_reset = await self.elo_service.ensure_current_season()
        if season_reset:
            await self._announce_elo_season_reset(guild, season)
        await self.refresh_elo_leaderboard(guild, season=season)
        await self.connect_resolver.refresh_dathost_connect_info()

    async def _get_guild_setup(self, guild: discord.Guild) -> GuildSetup | None:
        setup = self.guild_setups.get(guild.id)
        if setup is not None:
            return setup

        setup = await self.storage.get_guild_setup(guild.id)
        if setup is not None:
            self.guild_setups[guild.id] = setup
            return setup

        if self.settings.discord_guild_id == guild.id:
            return await self.ensure_guild_channels(guild)

        return None

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot:
            return

        setup = await self._get_guild_setup(member.guild)
        if setup is None:
            if self.settings.discord_guild_id != member.guild.id:
                return
            setup = await self.ensure_guild_channels(member.guild)

        await self._enforce_match_team_voice(member)

        before_mode = resolve_queue_mode(setup, before.channel)
        after_mode = resolve_queue_mode(setup, after.channel)

        if before_mode is None and after_mode is None:
            return

        if after_mode is not None and before_mode != after_mode:
            try:
                await self._require_linked_player_id(member.id)
            except ValueError:
                try:
                    await member.move_to(None)
                except discord.HTTPException:
                    pass
                if not await self.send_steam_link_dm(
                    member,
                    reason="You must link Steam before joining a queue voice channel.",
                ):
                    await self._notify_player(
                        member,
                        f"Link your Steam account with `{COMMAND_PREFIX}steamlink <steamID64>` or click **Link Steam Account** "
                        "on `#queue-status`. Enable DMs from server members to link via private message.",
                    )
                await self._sync_queues_from_voice(member.guild, setup)
                await self.refresh_queue_status(member.guild, sync_voice=False)
                return

        await self._sync_queues_from_voice(member.guild, setup)
        await self.refresh_queue_status(member.guild, sync_voice=False)

    def _build_queue_status_embed(self):
        default_map = self.settings.default_map
        return build_queue_embed(
            self.matchmaker,
            default_map,
            server_connect_field=(
                self._server_connect_field()
                if self._queue_should_show_server_connect(default_map)
                else None
            ),
            active_match_lines=self._active_match_details_lines(),
        )

    async def ensure_guild_channels(self, guild: discord.Guild) -> GuildSetup:
        stored = await self.storage.get_guild_setup(guild.id)
        setup = await ensure_guild_setup(guild, stored)
        await self.storage.save_guild_setup(setup)

        self.guild_setups[guild.id] = setup
        await self.ensure_status_message(guild, setup)
        season, season_reset = await self.elo_service.ensure_current_season()
        if season_reset:
            await self._announce_elo_season_reset(guild, season)
        await self.refresh_elo_leaderboard(guild, season=season)
        return setup

    async def ensure_status_message(
        self,
        guild: discord.Guild,
        setup: GuildSetup,
    ) -> None:
        channel = guild.get_channel(setup.status_channel_id)
        if not isinstance(channel, discord.TextChannel):
            setup = await ensure_guild_setup(guild, setup)
            await self.storage.save_guild_setup(setup)
            self.guild_setups[guild.id] = setup
            channel = guild.get_channel(setup.status_channel_id)
            if not isinstance(channel, discord.TextChannel):
                return

        embed = self._build_queue_status_embed()
        view = QueueControlView(self)

        if setup.status_message_id is not None:
            try:
                message = await channel.fetch_message(setup.status_message_id)
                await message.edit(embed=embed, view=view)
                await self._ensure_queue_reactions(message)
                return
            except discord.NotFound:
                pass

        message = await channel.send(embed=embed, view=view)
        await self._ensure_queue_reactions(message)
        setup = GuildSetup(
            guild_id=setup.guild_id,
            category_id=setup.category_id,
            status_channel_id=setup.status_channel_id,
            status_message_id=message.id,
            results_channel_id=setup.results_channel_id,
            elo_channel_id=setup.elo_channel_id,
            elo_message_id=setup.elo_message_id,
            voice_channels=setup.voice_channels,
        )
        self.guild_setups[guild.id] = setup
        await self.storage.save_guild_setup(setup)

    async def ensure_elo_leaderboard_message(
        self,
        guild: discord.Guild,
        setup: GuildSetup,
        *,
        season=None,
    ) -> None:
        if setup.elo_channel_id <= 0:
            setup = await ensure_guild_setup(guild, setup)
            await self.storage.save_guild_setup(setup)
            self.guild_setups[guild.id] = setup

        channel = guild.get_channel(setup.elo_channel_id)
        if not isinstance(channel, discord.TextChannel):
            setup = await ensure_guild_setup(guild, setup)
            await self.storage.save_guild_setup(setup)
            self.guild_setups[guild.id] = setup
            channel = guild.get_channel(setup.elo_channel_id)
            if not isinstance(channel, discord.TextChannel):
                return

        if season is None:
            season = await self.elo_service.get_current_season()
        boards = await self.elo_service.get_all_leaderboards()
        embed = build_leaderboard_embed(
            season,
            boards,
            default_elo=self.settings.default_elo,
        )

        if setup.elo_message_id is not None:
            try:
                message = await channel.fetch_message(setup.elo_message_id)
                await message.edit(embed=embed)
                return
            except discord.NotFound:
                pass

        message = await channel.send(embed=embed)
        try:
            await message.pin()
        except discord.HTTPException:
            logger.warning("Could not pin ELO leaderboard message in #%s", channel.name)

        setup = GuildSetup(
            guild_id=setup.guild_id,
            category_id=setup.category_id,
            status_channel_id=setup.status_channel_id,
            status_message_id=setup.status_message_id,
            results_channel_id=setup.results_channel_id,
            elo_channel_id=setup.elo_channel_id,
            elo_message_id=message.id,
            voice_channels=setup.voice_channels,
        )
        self.guild_setups[guild.id] = setup
        await self.storage.save_guild_setup(setup)

    async def refresh_elo_leaderboard(
        self,
        guild: discord.Guild,
        *,
        season=None,
    ) -> None:
        setup = await self._get_guild_setup(guild)
        if setup is None:
            return
        await self.ensure_elo_leaderboard_message(guild, setup, season=season)

    async def _announce_elo_season_reset(self, guild: discord.Guild, season) -> None:
        setup = await self._get_guild_setup(guild)
        if setup is None or setup.elo_channel_id <= 0:
            return

        channel = guild.get_channel(setup.elo_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        await channel.send(
            embed=discord.Embed(
                title="ELO season reset",
                description=(
                    f"A new **3-month season** has started: **{season.label}**.\n"
                    "All ratings were reset to the default starting ELO. "
                    "Play ranked matches to climb the leaderboard again."
                ),
                color=discord.Color.green(),
            )
        )

    async def _sync_queues_from_voice(
        self,
        guild: discord.Guild,
        setup: GuildSetup,
    ) -> None:
        default_map = self.settings.default_map
        for mode in MatchMode:
            channel = guild.get_channel(setup.voice_channels[mode])
            if not isinstance(channel, discord.VoiceChannel):
                continue

            in_voice_ids = {voice_member.id for voice_member in channel.members if not voice_member.bot}
            for entry in self.matchmaker.get_mode_entries(mode, default_map):
                if entry.discord_id not in in_voice_ids:
                    try:
                        self.matchmaker.leave_queue(entry.discord_id)
                    except ValueError:
                        pass
                    except Exception:
                        logger.exception(
                            "Failed to remove player %s from %s queue",
                            entry.discord_id,
                            mode.label,
                        )

            for voice_member in channel.members:
                if voice_member.bot:
                    continue
                queued = self.matchmaker.is_queued(voice_member.id)
                if queued is not None and queued[0] != mode:
                    try:
                        self.matchmaker.leave_queue(voice_member.id)
                    except ValueError:
                        continue
                if self.matchmaker.is_queued(voice_member.id) is not None:
                    continue
                try:
                    steam_id, discord_name = await self._require_linked_player_id(voice_member.id)
                except ValueError:
                    continue
                try:
                    self.matchmaker.enter_queue(
                        mode,
                        voice_member.id,
                        discord_name,
                        steam_id,
                        default_map,
                    )
                except ValueError:
                    pass

    async def refresh_queue_status(
        self,
        guild: discord.Guild,
        *,
        sync_voice: bool = True,
    ) -> None:
        setup = await self._get_guild_setup(guild)
        if setup is None:
            if self.settings.discord_guild_id != guild.id:
                return
            setup = await self.ensure_guild_channels(guild)
        if setup is None:
            return

        if sync_voice:
            await self._sync_queues_from_voice(guild, setup)

        if self._queue_should_show_server_connect(self.settings.default_map):
            await self.connect_resolver.refresh_dathost_connect_info()

        channel = guild.get_channel(setup.status_channel_id)
        if not isinstance(channel, discord.TextChannel):
            setup = await ensure_guild_setup(guild, setup)
            await self.storage.save_guild_setup(setup)
            self.guild_setups[guild.id] = setup
            channel = guild.get_channel(setup.status_channel_id)
            if not isinstance(channel, discord.TextChannel):
                return

        embed = self._build_queue_status_embed()
        view = QueueControlView(self)

        if setup.status_message_id is None:
            await self.ensure_status_message(guild, setup)
            await self._update_queue_ready_timers(guild)
            return

        try:
            message = await channel.fetch_message(setup.status_message_id)
            await message.edit(embed=embed, view=view)
            await self._ensure_queue_reactions(message)
        except discord.NotFound:
            await self.ensure_status_message(guild, setup)

        await self._update_queue_ready_timers(guild)

    def _queue_timer_key(self, mode: MatchMode, map_name: str) -> tuple[MatchMode, str]:
        return mode, map_name

    def _cancel_queue_ready_timer(self, mode: MatchMode, map_name: str) -> None:
        timer_key = self._queue_timer_key(mode, map_name)
        task = self._queue_ready_timer_tasks.pop(timer_key, None)
        if task is not None and not task.done():
            task.cancel()

    def _queue_ready_timeout_seconds(self, mode: MatchMode, queue_size: int) -> int:
        """Ready-up window once the queue is full (same duration for 1v1, 2v2, and 5v5)."""
        if queue_size < mode.total_players:
            return 0
        return max(60, self.settings.queue_ready_timeout_seconds)

    async def _update_queue_ready_timers(self, guild: discord.Guild) -> None:
        default_map = self.settings.default_map
        for mode in MatchMode:
            map_name = default_map
            entries = self.matchmaker.get_mode_entries(mode, map_name)
            queue_size = len(entries)
            if queue_size == 0:
                self._cancel_queue_ready_timer(mode, map_name)
                continue
            if self.matchmaker.all_queued_players_ready(mode, map_name):
                self._cancel_queue_ready_timer(mode, map_name)
                continue
            timeout_seconds = self._queue_ready_timeout_seconds(mode, queue_size)
            if timeout_seconds <= 0:
                self._cancel_queue_ready_timer(mode, map_name)
                continue
            await self._schedule_queue_ready_timer(
                guild,
                mode,
                map_name,
                timeout_seconds,
            )

    async def _schedule_queue_ready_timer(
        self,
        guild: discord.Guild,
        mode: MatchMode,
        map_name: str,
        timeout_seconds: int,
    ) -> None:
        timer_key = self._queue_timer_key(mode, map_name)
        self._cancel_queue_ready_timer(mode, map_name)
        self._queue_ready_timer_tasks[timer_key] = asyncio.create_task(
            self._queue_ready_timeout(guild.id, mode, map_name, timeout_seconds),
            name=f"queue-ready-timeout-{mode.value}-{map_name}",
        )

    async def _queue_ready_timeout(
        self,
        guild_id: int,
        mode: MatchMode,
        map_name: str,
        timeout_seconds: int,
    ) -> None:
        try:
            await asyncio.sleep(timeout_seconds)
        except asyncio.CancelledError:
            return

        timer_key = self._queue_timer_key(mode, map_name)
        self._queue_ready_timer_tasks.pop(timer_key, None)

        guild = self.get_guild(guild_id)
        if guild is None:
            return

        entries = self.matchmaker.get_mode_entries(mode, map_name)
        if len(entries) < mode.total_players:
            return
        if self.matchmaker.all_queued_players_ready(mode, map_name):
            return

        timeout_label = (
            f"{timeout_seconds // 60} minutes"
            if timeout_seconds >= 60 and timeout_seconds % 60 == 0
            else f"{timeout_seconds} seconds"
        )
        await self._cancel_queue_for_timeout(
            guild,
            mode,
            map_name,
            reason=(
                f"not everyone readied up within {timeout_label} "
                f"({len(entries)}/{mode.total_players} players). "
                "Rejoin the queue voice channel to try again."
            ),
        )

    async def _cancel_queue_for_timeout(
        self,
        guild: discord.Guild,
        mode: MatchMode,
        map_name: str,
        *,
        reason: str,
    ) -> None:
        removed_ids = self.matchmaker.clear_queue(mode, map_name)
        self._cancel_queue_ready_timer(mode, map_name)

        setup = await self._get_guild_setup(guild)
        if setup is not None:
            voice_channel = guild.get_channel(setup.voice_channels[mode])
            if isinstance(voice_channel, discord.VoiceChannel):
                for discord_id in removed_ids:
                    member = guild.get_member(discord_id)
                    if member is None:
                        continue
                    if (
                        member.voice is not None
                        and member.voice.channel is not None
                        and member.voice.channel.id == voice_channel.id
                    ):
                        try:
                            await member.move_to(None)
                        except discord.HTTPException:
                            pass

            status_channel = guild.get_channel(setup.status_channel_id)
            if isinstance(status_channel, discord.TextChannel):
                await status_channel.send(
                    f"**{mode.label}** queue on `{map_name}` was cancelled — {reason}"
                )

        await self.refresh_queue_status(guild, sync_voice=False)

    async def _ensure_queue_reactions(self, message: discord.Message) -> None:
        for emoji in (READY_EMOJI, UNREADY_EMOJI):
            if not any(str(reaction.emoji) == emoji for reaction in message.reactions):
                try:
                    await message.add_reaction(emoji)
                except discord.HTTPException:
                    logger.exception("Failed to add %s reaction to queue status message", emoji)

    async def _resolve_reaction_member(
        self,
        guild: discord.Guild,
        user_id: int,
    ) -> discord.Member | None:
        member = guild.get_member(user_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user_id)
        except discord.HTTPException:
            return None

    async def _sync_ready_reactions(
        self,
        message: discord.Message,
        member: discord.Member,
        ready: bool,
    ) -> None:
        self._reaction_sync_user_ids.add(member.id)
        try:
            remove_emoji = UNREADY_EMOJI if ready else READY_EMOJI
            try:
                await message.remove_reaction(remove_emoji, member)
            except discord.HTTPException:
                pass
        finally:
            self._reaction_sync_user_ids.discard(member.id)

    async def _handle_queue_ready_reaction(
        self,
        payload: discord.RawReactionActionEvent,
        *,
        ready: bool,
    ) -> None:
        if payload.user_id == self.user.id:
            return
        if payload.user_id in self._reaction_sync_user_ids:
            return

        guild = self.get_guild(payload.guild_id)
        if guild is None:
            return

        setup = await self._get_guild_setup(guild)
        if setup is None or payload.message_id != setup.status_message_id:
            return

        member = await self._resolve_reaction_member(guild, payload.user_id)
        if member is None:
            return

        channel = guild.get_channel(payload.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.HTTPException:
            return

        try:
            await self._apply_ready_toggle(guild, member, ready)
        except ValueError as exc:
            try:
                await message.remove_reaction(payload.emoji, member)
            except discord.HTTPException:
                pass
            await self._notify_player(member, str(exc), offer_steam_link="Steam" in str(exc))
            return

        await self._sync_ready_reactions(message, member, ready)

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        emoji = str(payload.emoji)
        if emoji == READY_EMOJI:
            await self._handle_queue_ready_reaction(payload, ready=True)
        elif emoji == UNREADY_EMOJI:
            await self._handle_queue_ready_reaction(payload, ready=False)

    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.user_id in self._reaction_sync_user_ids:
            return
        if str(payload.emoji) != READY_EMOJI:
            return
        entry = self.matchmaker.get_entry(payload.user_id)
        if entry is None or not entry[1].ready:
            return
        await self._handle_queue_ready_reaction(payload, ready=False)

    async def _require_linked_player_id(self, discord_id: int) -> tuple[str, str]:
        profile = await self.storage.get_player(discord_id)
        if profile is None:
            raise ValueError(f"Link your Steam account first with `{COMMAND_PREFIX}steamlink <steamID64>`.")
        return profile

    def _console_error_label(self) -> str:
        if self.settings.server_provider == ServerProvider.DATHOST:
            return "DatHost console error"
        return "RCON error"

    def is_bot_admin(self, member: discord.Member) -> bool:
        if member.guild_permissions.administrator:
            return True
        role_id = self.settings.discord_admin_role_id
        if role_id is None:
            return False
        return any(role.id == role_id for role in member.roles)

    def _require_bot_admin(
        self,
        ctx: commands.Context,
        *,
        allow_manage_channels: bool = False,
    ) -> discord.Member:
        if ctx.guild is None:
            raise commands.NoPrivateMessage()

        member = ctx.author
        if not isinstance(member, discord.Member):
            raise commands.CheckFailure("Could not resolve your member profile.")

        if self.is_bot_admin(member):
            return member

        if allow_manage_channels and member.guild_permissions.manage_channels:
            return member

        if self.settings.discord_admin_role_id is not None:
            raise commands.CheckFailure(
                "You need the configured admin role or server administrator permission."
            )
        raise commands.CheckFailure(
            "You need server administrator permission to use this command."
        )

    async def link_steam_account(
        self,
        discord_id: int,
        display_name: str,
        steam_value: str,
    ) -> tuple[bool, str]:
        try:
            normalized = normalize_steam_id(steam_value)
        except ValueError as exc:
            return False, str(exc)

        try:
            await self.storage.upsert_player(discord_id, normalized, display_name)
        except sqlite3.IntegrityError:
            return False, "That Steam ID is already linked to another Discord account."
        except Exception:
            logger.exception("Failed to link Steam profile for user %s", discord_id)
            return False, "Could not save your Steam ID. Try again or ask an admin to check bot logs."

        return True, f"Linked Steam ID `{normalized}` to your Discord account."

    def _player_in_active_match(self, discord_id: int) -> bool:
        for match in self.matchmaker.active_matches.values():
            roster_ids = {player.discord_id for player in match.team1 + match.team2}
            if discord_id in roster_ids:
                return True
        return False

    async def unlink_steam_account(self, discord_id: int) -> tuple[bool, str]:
        profile = await self.storage.get_player(discord_id)
        if profile is None:
            return False, "No Steam account is linked to your Discord account."

        if self._player_in_active_match(discord_id):
            return False, "You cannot unlink while you are in an active match."

        steam_id, _ = profile
        if self.matchmaker.is_queued(discord_id):
            self.matchmaker.leave_queue(discord_id)

        deleted = await self.storage.delete_player(discord_id)
        if not deleted:
            return False, "Could not unlink your Steam account. Try again or ask an admin."

        return True, f"Unlinked Steam ID `{steam_id}` from your Discord account."

    async def send_steam_link_dm(
        self,
        member: discord.Member,
        *,
        reason: str | None = None,
    ) -> bool:
        embed = discord.Embed(
            title="Link your Steam account",
            description=(
                "Matchmaking requires your **steamID64** (17 digits).\n\n"
                "1. Open https://steamid.io and copy your **steamID64**\n"
                "2. Click **Link Steam Account** below and paste it\n"
                "3. Use **Unlink Steam** to remove a linked account\n\n"
                f"You can also use `{COMMAND_PREFIX}steamlink <steamID64>` in the server."
            ),
            color=discord.Color.orange(),
        )
        if reason:
            embed.add_field(name="Why am I seeing this?", value=reason, inline=False)

        try:
            await member.send(embed=embed, view=SteamLinkDmView(self))
            return True
        except discord.HTTPException:
            return False

    async def handle_steam_link_request(self, interaction: discord.Interaction) -> None:
        profile = await self.storage.get_player(interaction.user.id)
        if profile is not None:
            steam_id, _ = profile
            await interaction.response.send_message(
                f"Your Steam ID `{steam_id}` is already linked. "
                "Click **Unlink Steam** on `#queue-status` or in your link DM to remove it.",
                ephemeral=True,
            )
            return

        if isinstance(interaction.user, discord.Member):
            sent = await self.send_steam_link_dm(
                interaction.user,
                reason="Link your account before joining matchmaking.",
            )
            if sent:
                await interaction.response.send_message(
                    "Check your **DMs** from this bot and click **Link Steam Account**.",
                    ephemeral=True,
                )
                return

        await interaction.response.send_modal(SteamLinkModal(self))

    async def handle_steam_unlink_request(self, interaction: discord.Interaction) -> None:
        profile = await self.storage.get_player(interaction.user.id)
        if profile is None:
            await interaction.response.send_message(
                "No Steam account is linked to your Discord account.",
                ephemeral=True,
            )
            return

        steam_id, _ = profile
        await interaction.response.send_message(
            f"Unlink Steam ID `{steam_id}` from your Discord account?\n"
            "You will need to link again before joining matchmaking.",
            view=SteamUnlinkConfirmView(self),
            ephemeral=True,
        )

    async def _notify_player(
        self,
        member: discord.Member,
        message: str,
        *,
        offer_steam_link: bool = False,
    ) -> None:
        if offer_steam_link and isinstance(member, discord.Member):
            if await self.send_steam_link_dm(member, reason=message):
                return

        try:
            await member.send(message)
        except discord.HTTPException:
            setup = self.guild_setups.get(member.guild.id)
            if setup is None:
                return
            channel = member.guild.get_channel(setup.status_channel_id)
            if isinstance(channel, discord.TextChannel):
                await channel.send(f"{member.mention} {message}")

    async def _get_match_voice_ids(self, match_id: str) -> tuple[int, int] | None:
        cached = self._match_voice_channels.get(match_id)
        if cached is not None:
            return cached
        stored = await self.storage.get_match_voice_channels(match_id)
        if stored is not None:
            self._match_voice_channels[match_id] = stored
        return stored

    async def _find_match_for_voice_channel(
        self,
        channel_id: int,
    ) -> tuple[ActiveMatch, int, int] | None:
        for match in self.matchmaker.active_matches.values():
            voice_ids = await self._get_match_voice_ids(match.match_id)
            if voice_ids is None:
                continue
            team1_id, team2_id = voice_ids
            if channel_id in {team1_id, team2_id}:
                return match, team1_id, team2_id
        return None

    async def _enforce_match_team_voice(self, member: discord.Member) -> None:
        if member.voice is None or member.voice.channel is None:
            return

        active = await self._find_match_for_voice_channel(member.voice.channel.id)
        if active is None:
            return

        match, team1_id, team2_id = active
        await enforce_player_team_voice(
            member.guild,
            member,
            match,
            team1_id,
            team2_id,
        )

    def _build_match_embed(
        self,
        match: ActiveMatch,
        team1_voice_id: int | None = None,
        team2_voice_id: int | None = None,
    ) -> discord.Embed:
        public_host = self.connect_resolver.get_connect_host()
        public_port = self.connect_resolver.get_connect_port()
        server_password = self.connect_resolver.get_connect_password()

        embed = discord.Embed(
            title=f"{match.mode.label} match ready",
            description=(
                "You have been moved to your **team voice channel**. "
                "Only players in this match can join CT/T voice. "
                "Connect to the CS2 server — MatchZy assigns your in-game team from your linked Steam ID.\n"
                "Type `.ready` in game chat when connected.\n"
                f"Match ID: `{match.match_id}`"
            ),
            color=discord.Color.green(),
        )
        alpha_side = match.team1_side
        alpha_label = "CT" if alpha_side == "ct" else "T"
        bravo_label = "T" if alpha_side == "ct" else "CT"
        embed.add_field(
            name="Sides",
            value=f"Team Alpha: **{alpha_label}** · Team Bravo: **{bravo_label}**",
            inline=True,
        )
        embed.add_field(name="Map", value=f"`{match.map_name}`", inline=True)
        embed.add_field(
            name="Join server",
            value=build_server_connect_field(public_host, public_port, server_password or None),
            inline=False,
        )
        if team1_voice_id is not None and team2_voice_id is not None:
            embed.add_field(
                name="Match voice",
                value=f"CT: <#{team1_voice_id}>\nT: <#{team2_voice_id}>",
                inline=False,
            )
        embed.add_field(
            name="Teams",
            value=(
                f"{format_team(match.team1, 'Team Alpha')}\n\n"
                f"{format_team(match.team2, 'Team Bravo')}"
            ),
            inline=False,
        )
        return embed

    def _server_connect_field(self) -> str:
        public_host = self.connect_resolver.get_connect_host()
        public_port = self.connect_resolver.get_connect_port()
        server_password = self.connect_resolver.get_connect_password()
        return build_server_connect_field(public_host, public_port, server_password or None)

    def _queue_should_show_server_connect(self, default_map: str) -> bool:
        if self.matchmaker.active_matches:
            return True
        for mode in MatchMode:
            if self.matchmaker.all_queued_players_ready(mode, default_map):
                return True
            captain_flow = self.matchmaker.get_captain_flow(mode, default_map)
            if captain_flow.phase != CaptainPhase.NONE:
                return True
            map_flow = self.matchmaker.get_premier_veto_flow(mode, default_map)
            if map_flow.phase != PremierVetoPhase.NONE:
                return True
        return False

    def _active_match_details_lines(self) -> list[str]:
        lines: list[str] = []
        for match in self.matchmaker.active_matches.values():
            lines.append(
                f"**{match.mode.label}** on `{match.map_name}` — Match ID `{match.match_id}`"
            )
        return lines

    async def _setup_match_voice_channels(
        self,
        guild: discord.Guild,
        match: ActiveMatch,
    ) -> tuple[int, int] | None:
        setup = await self._get_guild_setup(guild)
        if setup is None:
            return None

        category = guild.get_channel(setup.category_id)
        if not isinstance(category, discord.CategoryChannel):
            return None

        try:
            voice_channels = await create_match_voice_channels(guild, category, match)
        except discord.HTTPException:
            logger.exception("Failed to create team voice channels for match %s", match.match_id)
            return None

        self._match_voice_channels[match.match_id] = (
            voice_channels.team1_channel_id,
            voice_channels.team2_channel_id,
        )
        saved = await self.storage.save_match_voice_channels(
            match.match_id,
            voice_channels.team1_channel_id,
            voice_channels.team2_channel_id,
        )
        if not saved:
            logger.error(
                "Failed to persist team voice channel ids for match %s",
                match.match_id,
            )
        await move_players_to_team_channels(
            guild,
            match,
            voice_channels.team1_channel_id,
            voice_channels.team2_channel_id,
        )
        return voice_channels.team1_channel_id, voice_channels.team2_channel_id

    async def _active_match_ids(self) -> list[str]:
        active_ids = list(self.matchmaker.active_matches.keys())
        if active_ids:
            return active_ids
        return await self.storage.get_active_match_ids()

    async def _resolve_match_id_for_event(
        self,
        raw_match_id: str,
        *,
        allow_single_active_fallback: bool = False,
    ) -> str | None:
        normalized = str(raw_match_id).strip()
        if normalized and normalized != "unknown":
            if normalized in self.matchmaker.active_matches:
                return normalized
            resolved = await self.elo_service.resolve_match_id(normalized)
            if resolved is not None:
                return resolved

        if not allow_single_active_fallback:
            return None

        active_ids = await self._active_match_ids()
        if not active_ids:
            return None
        if len(active_ids) == 1:
            return active_ids[0]

        if normalized.isdigit():
            for match_id in active_ids:
                if match_id == normalized:
                    return match_id
        return None

    async def _ensure_guild_for_events(self) -> discord.Guild | None:
        if self.settings.discord_guild_id is None:
            logger.warning("DISCORD_GUILD_ID is not set; ignoring MatchZy event")
            return None

        guild = self.get_guild(self.settings.discord_guild_id)
        if guild is None:
            try:
                guild = await self.fetch_guild(self.settings.discord_guild_id)
            except discord.HTTPException:
                logger.exception(
                    "Could not fetch guild %s for MatchZy event",
                    self.settings.discord_guild_id,
                )
                return None

        if guild.id not in self.guild_setups:
            setup = await self.storage.get_guild_setup(guild.id)
            if setup is not None:
                self.guild_setups[guild.id] = setup

        return guild

    async def _get_match_display_data(
        self,
        match_id: str,
    ) -> tuple[MatchMode, str, list[int], list[int], dict[int, str], dict[int, str]] | None:
        roster = await self.elo_service.get_roster(match_id)
        if roster is not None:
            record = await self.storage.get_match_record(match_id)
            map_name = record["map_name"] if record is not None else ""
            return (
                roster.mode,
                map_name,
                roster.team1_ids,
                roster.team2_ids,
                roster.team1_names,
                roster.team2_names,
            )

        active_match = self.matchmaker.get_match(match_id)
        record = await self.storage.get_match_record(match_id)
        if active_match is None and record is None:
            return None

        if active_match is not None:
            mode = active_match.mode
            map_name = active_match.map_name
            team1_ids = [player.discord_id for player in active_match.team1]
            team2_ids = [player.discord_id for player in active_match.team2]
            team1_names = {player.discord_id: player.discord_name for player in active_match.team1}
            team2_names = {player.discord_id: player.discord_name for player in active_match.team2}
        else:
            mode = MatchMode(record["mode"])
            map_name = record["map_name"]
            stored_roster = record.get("roster") or {}
            team1_ids = stored_roster.get("team1_ids", [])
            team2_ids = stored_roster.get("team2_ids", [])
            team1_names = stored_roster.get("team1_names", {})
            team2_names = stored_roster.get("team2_names", {})

        if not map_name and record is not None:
            map_name = record["map_name"]

        return mode, map_name, team1_ids, team2_ids, team1_names, team2_names

    def _cancel_match_finish_fallback(self, match_id: str) -> None:
        task = self._match_finish_fallback_tasks.pop(match_id, None)
        if task is not None and not task.done():
            task.cancel()

    def _schedule_match_finish_fallback(
        self,
        guild: discord.Guild,
        match_id: str,
        payload: dict,
        *,
        delay_seconds: int = 20,
    ) -> None:
        self._cancel_match_finish_fallback(match_id)

        async def _fallback() -> None:
            try:
                await asyncio.sleep(delay_seconds)
            except asyncio.CancelledError:
                return

            record = await self.storage.get_match_record(match_id)
            if record is not None and record.get("status") == "completed":
                return

            logger.warning(
                "No series_end received for match %s; finishing from map_result fallback",
                match_id,
            )
            await self._finish_match_from_event(guild, match_id, payload)

        self._match_finish_fallback_tasks[match_id] = asyncio.create_task(
            _fallback(),
            name=f"match-finish-fallback-{match_id}",
        )

    async def _resolve_match_voice_ids(
        self,
        guild: discord.Guild,
        match_id: str,
    ) -> tuple[int, int] | None:
        cached = self._match_voice_channels.get(match_id)
        if cached is not None:
            return cached

        stored = await self.storage.get_match_voice_channels(match_id)
        if stored is not None:
            self._match_voice_channels[match_id] = stored
            return stored

        discovered = await find_match_voice_channels_by_name(guild, match_id)
        if discovered is not None:
            logger.info(
                "Discovered team voice channels for match %s by name: %s, %s",
                match_id,
                discovered[0],
                discovered[1],
            )
            self._match_voice_channels[match_id] = discovered
            await self.storage.save_match_voice_channels(
                match_id,
                discovered[0],
                discovered[1],
            )
        return discovered

    async def _get_match_roster_ids(self, match_id: str) -> set[int]:
        active_match = self.matchmaker.get_match(match_id)
        if active_match is not None:
            return {player.discord_id for player in active_match.team1 + active_match.team2}

        roster = await self.elo_service.get_roster(match_id)
        if roster is None:
            return set()
        return set(roster.team1_ids + roster.team2_ids)

    async def _ensure_end_queue_channel(
        self,
        guild: discord.Guild,
    ) -> int | None:
        setup = await self._get_guild_setup(guild)
        if setup is None:
            setup = await self.ensure_guild_channels(guild)
        else:
            end_queue = guild.get_channel(setup.end_queue_channel_id)
            if not isinstance(end_queue, discord.VoiceChannel):
                setup = await ensure_guild_setup(guild, setup)
                await self.storage.save_guild_setup(setup)
                self.guild_setups[guild.id] = setup

        if setup.end_queue_channel_id:
            return setup.end_queue_channel_id

        setup = await ensure_guild_setup(guild, setup)
        await self.storage.save_guild_setup(setup)
        self.guild_setups[guild.id] = setup
        return setup.end_queue_channel_id or None

    async def _delete_match_voice_channels(
        self,
        guild: discord.Guild,
        match_id: str,
        team1_id: int,
        team2_id: int,
    ) -> None:
        await delete_match_voice_channels(guild, team1_id, team2_id)
        self._match_voice_channels.pop(match_id, None)
        await self.storage.clear_match_voice_channels(match_id)

    async def cleanup_match(
        self,
        guild: discord.Guild,
        match_id: str,
        *,
        cancelled: bool = False,
    ) -> None:
        record = await self.storage.get_match_record(match_id)
        already_completed = record is not None and record.get("status") == "completed"

        voice_ids = await self._resolve_match_voice_ids(guild, match_id)

        if voice_ids is not None:
            team1_id, team2_id = voice_ids
            roster_ids = await self._get_match_roster_ids(match_id)
            if roster_ids:
                end_queue_id = await self._ensure_end_queue_channel(guild)
                if end_queue_id is not None:
                    await move_match_players_to_end_queue(
                        guild,
                        roster_ids,
                        team1_id,
                        team2_id,
                        end_queue_id,
                    )
            await self._delete_match_voice_channels(guild, match_id, team1_id, team2_id)
            logger.info(
                "Cleaned up team voice channels for match %s (%s, %s)",
                match_id,
                team1_id,
                team2_id,
            )
        elif not already_completed:
            logger.warning(
                "No team voice channels stored for match %s during cleanup",
                match_id,
            )

        if already_completed:
            self.matchmaker.finish_match(match_id)
            return

        await self.storage.clear_match_voice_returns(match_id)

        self._cancel_match_finish_fallback(match_id)

        await self.storage.update_match_status(match_id, "completed")
        self.matchmaker.finish_match(match_id)

        if cancelled:
            await self._finalize_cancelled_live_match(guild, match_id)
        await self._clear_match_status_message(guild, match_id)
        self._live_match_snapshots.pop(match_id, None)

        setup = await self._get_guild_setup(guild)
        if setup is not None:
            channel = guild.get_channel(setup.status_channel_id)
            if isinstance(channel, discord.TextChannel):
                end_queue = guild.get_channel(setup.end_queue_channel_id)
                end_queue_mention = (
                    f" Players moved to {end_queue.mention}."
                    if isinstance(end_queue, discord.VoiceChannel)
                    else " Players moved to **End Queue**."
                )
                end_note = (
                    f"cancelled by an admin.{end_queue_mention}"
                    if cancelled
                    else f"ended.{end_queue_mention}"
                )
                await channel.send(f"Match `{match_id}` {end_note}")

        await self.refresh_queue_status(guild)

    async def _get_results_channel(
        self,
        guild: discord.Guild,
    ) -> discord.TextChannel | None:
        setup = await self._get_guild_setup(guild)
        if setup is None:
            return None

        results_channel = guild.get_channel(setup.results_channel_id)
        if not isinstance(results_channel, discord.TextChannel):
            setup = await ensure_guild_setup(guild, setup)
            await self.storage.save_guild_setup(setup)
            self.guild_setups[guild.id] = setup
            results_channel = guild.get_channel(setup.results_channel_id)
            if not isinstance(results_channel, discord.TextChannel):
                return None
        return results_channel

    async def _post_live_match_embed(
        self,
        guild: discord.Guild,
        match: ActiveMatch,
    ) -> None:
        results_channel = await self._get_results_channel(guild)
        if results_channel is None:
            return

        snapshot = LiveMatchSnapshot(status="Match deployed — waiting for server")
        self._live_match_snapshots[match.match_id] = snapshot

        roster = await self.elo_service.get_roster(match.match_id)
        if roster is None:
            team1_ids = [player.discord_id for player in match.team1]
            team2_ids = [player.discord_id for player in match.team2]
            team1_names = {player.discord_id: player.discord_name for player in match.team1}
            team2_names = {player.discord_id: player.discord_name for player in match.team2}
        else:
            team1_ids = roster.team1_ids
            team2_ids = roster.team2_ids
            team1_names = roster.team1_names
            team2_names = roster.team2_names

        embed = build_live_match_embed(
            match.match_id,
            match.mode,
            match.map_name,
            team1_ids,
            team2_ids,
            team1_names,
            team2_names,
            snapshot,
            server_connect_field=self._server_connect_field(),
        )
        message = await results_channel.send(embed=embed)
        await self.storage.save_live_results_message_id(match.match_id, message.id)

    async def _update_live_match_embed(
        self,
        guild: discord.Guild,
        match_id: str,
        event_name: str,
        payload: dict,
    ) -> None:
        if event_name not in LIVE_UPDATE_EVENTS:
            return

        snapshot = self._live_match_snapshots.setdefault(match_id, LiveMatchSnapshot())
        snapshot.merge_event(event_name, payload)

        display = await self._get_match_display_data(match_id)
        if display is None:
            logger.warning("No roster/record for live update on match %s", match_id)
            return

        mode, map_name, team1_ids, team2_ids, team1_names, team2_names = display
        results_channel = await self._get_results_channel(guild)
        if results_channel is None:
            logger.warning("No #match-results channel configured for live update")
            return

        embed = build_live_match_embed(
            match_id,
            mode,
            map_name,
            team1_ids,
            team2_ids,
            team1_names,
            team2_names,
            snapshot,
            server_connect_field=self._server_connect_field(),
        )

        message_id = await self.storage.get_live_results_message_id(match_id)
        if message_id is None:
            message = await results_channel.send(embed=embed)
            await self.storage.save_live_results_message_id(match_id, message.id)
            logger.info("Created live match message for %s during %s", match_id, event_name)
            return

        try:
            message = await results_channel.fetch_message(message_id)
            await message.edit(embed=embed)
        except discord.NotFound:
            message = await results_channel.send(embed=embed)
            await self.storage.save_live_results_message_id(match_id, message.id)
            logger.warning("Recreated missing live match message for %s", match_id)
        except discord.HTTPException:
            logger.exception("Failed to update live match embed for %s", match_id)

    async def _finalize_cancelled_live_match(self, guild: discord.Guild, match_id: str) -> None:
        message_id = await self.storage.get_live_results_message_id(match_id)
        if message_id is None:
            return

        results_channel = await self._get_results_channel(guild)
        if results_channel is None:
            return

        record = await self.storage.get_match_record(match_id)
        roster = await self.elo_service.get_roster(match_id)
        if record is None or roster is None:
            await self.storage.clear_live_results_message_id(match_id)
            return

        embed = discord.Embed(
            title="Match cancelled",
            description=f"**{MatchMode(record['mode']).label}** on `{record['map_name']}`",
            color=discord.Color.light_grey(),
        )
        embed.set_footer(text=f"Match ID: {match_id} · No ELO change")
        embed.timestamp = discord.utils.utcnow()

        try:
            message = await results_channel.fetch_message(message_id)
            await message.edit(embed=embed)
        except discord.HTTPException:
            logger.exception("Failed to mark live match %s as cancelled", match_id)

        await self.storage.clear_live_results_message_id(match_id)

    async def _post_match_result(
        self,
        guild: discord.Guild,
        match_id: str,
        payload: dict,
        elo_changes: list | None,
    ) -> None:
        results_channel = await self._get_results_channel(guild)
        if results_channel is None:
            logger.warning("No #match-results channel configured for match %s", match_id)
            return

        display = await self._get_match_display_data(match_id)
        if display is None:
            logger.warning("No roster/record for final result on match %s", match_id)
            return

        mode, map_name, team1_ids, team2_ids, team1_names, team2_names = display
        embed = build_match_result_embed(
            match_id=match_id,
            mode=mode,
            map_name=map_name,
            team1_ids=team1_ids,
            team2_ids=team2_ids,
            team1_names=team1_names,
            team2_names=team2_names,
            payload=payload,
            elo_changes=elo_changes,
        )

        message_id = await self.storage.get_live_results_message_id(match_id)
        if message_id is not None:
            try:
                message = await results_channel.fetch_message(message_id)
                await message.edit(embed=embed)
                logger.info("Updated live match message with final result for %s", match_id)
            except discord.NotFound:
                await results_channel.send(embed=embed)
                logger.info("Posted final match result for %s (live message missing)", match_id)
            except discord.HTTPException:
                logger.exception("Failed to finalize live match result for %s", match_id)
                await results_channel.send(embed=embed)
        else:
            await results_channel.send(embed=embed)
            logger.info("Posted final match result for %s", match_id)

        await self.storage.clear_live_results_message_id(match_id)

    async def _ensure_dathost_server_ready(self) -> None:
        if self.settings.server_provider != ServerProvider.DATHOST:
            return
        if (
            not self.settings.dathost_email
            or not self.settings.dathost_password
            or not self.settings.dathost_game_server_id
        ):
            return

        from dathost_client import DatHostClient

        client = DatHostClient(
            email=self.settings.dathost_email,
            password=self.settings.dathost_password,
            server_id=self.settings.dathost_game_server_id,
            base_url=self.settings.dathost_api_base,
        )
        info = await client.get_server()
        if not info.online:
            logger.info("Starting DatHost server %s", info.server_id)
            await client.start_server()
        await self.connect_resolver.refresh_dathost_connect_info()

    async def announce_match(self, guild: discord.Guild, match: ActiveMatch) -> None:
        self._cancel_queue_ready_timer(match.mode, match.map_name)
        payload = serialize_match_config(match, self.settings)
        await self.storage.save_match(match.match_id, match.mode.value, match.map_name, payload)
        await self.storage.set_next_match_id(self.matchmaker.next_match_id)
        await self.elo_service.save_roster_from_match(match)

        team_voice_ids = await self._setup_match_voice_channels(guild, match)
        if team_voice_ids is None:
            await self._rollback_failed_match(
                guild,
                match,
                RuntimeError("Could not create team voice channels"),
            )
            return

        try:
            await self._ensure_dathost_server_ready()
            responses = await self.matchzy.deploy_match(match)
            logger.info("Match %s deployed: %s", match.match_id, responses)
        except Exception as exc:
            logger.exception("Failed to deploy match %s", match.match_id)
            await self._rollback_failed_match(guild, match, exc)
            return

        team1_voice_id, team2_voice_id = team_voice_ids

        embed = self._build_match_embed(match, team1_voice_id, team2_voice_id)
        setup = self.guild_setups.get(guild.id)
        if setup is not None:
            channel = guild.get_channel(setup.status_channel_id)
            if isinstance(channel, discord.TextChannel):
                message = await channel.send(embed=embed)
                self._match_status_messages[match.match_id] = message.id

        for player in match.team1 + match.team2:
            user = self.get_user(player.discord_id) or await self.fetch_user(player.discord_id)
            try:
                await user.send(embed=embed)
            except discord.HTTPException:
                logger.warning("Could not DM Discord user %s", player.discord_id)

        await self._post_live_match_embed(guild, match)
        await self.refresh_queue_status(guild)

    async def _clear_match_status_message(self, guild: discord.Guild, match_id: str) -> None:
        message_id = self._match_status_messages.pop(match_id, None)
        if message_id is None:
            return

        setup = await self._get_guild_setup(guild)
        if setup is None:
            return

        channel = guild.get_channel(setup.status_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        try:
            message = await channel.fetch_message(message_id)
            await message.delete()
        except discord.NotFound:
            pass
        except discord.HTTPException:
            logger.warning("Could not delete match status message %s", message_id)

    async def _rollback_failed_match(
        self,
        guild: discord.Guild,
        match: ActiveMatch,
        error: Exception,
    ) -> None:
        voice_ids = await self._resolve_match_voice_ids(guild, match.match_id)
        if voice_ids is not None:
            await self._delete_match_voice_channels(
                guild,
                match.match_id,
                voice_ids[0],
                voice_ids[1],
            )

        self.matchmaker.restore_match_players_to_queue(match)
        await self.storage.update_match_status(match.match_id, "cancelled")

        setup = await self._get_guild_setup(guild)
        if setup is not None:
            channel = guild.get_channel(setup.status_channel_id)
            if isinstance(channel, discord.TextChannel):
                await channel.send(
                    f"Match `{match.match_id}` could not start: `{error}`\n"
                    "Players were returned to the queue. Use "
                    f"`{COMMAND_PREFIX}admin endmatch` if the server "
                    "still has a match loaded."
                )

        await self.refresh_queue_status(guild)

    async def _apply_ready_toggle(
        self,
        guild: discord.Guild,
        member: discord.Member,
        ready: bool,
    ) -> tuple[str, ActiveMatch | None]:
        setup = await self._get_guild_setup(guild)

        in_queue_voice = (
            member.voice is not None
            and member.voice.channel is not None
            and setup is not None
            and setup.is_queue_voice_channel(member.voice.channel.id)
        )
        if not in_queue_voice:
            raise ValueError("Join a **Queue » 1v1 / 2v2 / 5v5** voice channel first.")

        steam_id, discord_name = await self._require_linked_player_id(member.id)

        if self.matchmaker.is_queued(member.id) is None:
            mode = setup.mode_for_voice_channel(member.voice.channel.id)
            if mode is not None:
                self.matchmaker.enter_queue(mode, member.id, discord_name, steam_id)

        _, map_name, match, voting_started = self.matchmaker.set_ready(member.id, ready)
        await self.refresh_queue_status(guild)

        entry_data = self.matchmaker.get_entry(member.id)
        mode = entry_data[0][0] if entry_data else MatchMode.FIVE_V_FIVE

        if ready:
            ready_count = sum(
                1 for entry in self.matchmaker.get_mode_entries(mode, map_name) if entry.ready
            )
            flow = self.matchmaker.get_captain_flow(mode, map_name)
            veto_flow = self.matchmaker.get_premier_veto_flow(mode, map_name)
            if (
                self.matchmaker.captains_required(mode)
                and flow.phase == CaptainPhase.VOTING
            ):
                message = (
                    f"You are **ready** for `{map_name}`. "
                    "Captain voting is open — click **Vote Captains** in #queue-status."
                )
            elif (
                self.matchmaker.captains_required(mode)
                and flow.phase == CaptainPhase.DRAFTING
            ):
                message = (
                    f"You are **ready** for `{map_name}`. "
                    "Player draft in progress — captains use **Pick Player** when it is their turn."
                )
            elif veto_flow.phase == PremierVetoPhase.BANNING:
                message = (
                    f"You are **ready** for **{mode.label}**. "
                    "Premier map veto is open — captains use **Ban Map** in #queue-status."
                )
            elif veto_flow.phase == PremierVetoPhase.SIDE_PICK:
                message = (
                    f"You are **ready** for **{mode.label}**. "
                    "Side pick is open — the designated captain uses **Pick Side** (CT/T)."
                )
            elif (
                self.matchmaker.captains_required(mode)
                and ready_count >= mode.total_players
            ):
                message = (
                    f"You are **ready** for `{map_name}`. "
                    "Enough players are ready — captain voting will begin shortly."
                )
            elif mode == MatchMode.ONE_V_ONE and ready_count >= mode.total_players:
                message = (
                    "You are **ready** for **1v1**. "
                    "Enough players are ready — Premier map veto will begin shortly."
                )
            else:
                message = f"You are **ready** for `{map_name}`. Waiting for other players..."
        else:
            message = "You are **not ready**."

        if voting_started:
            setup = await self._get_guild_setup(guild)
            if setup is not None:
                channel = guild.get_channel(setup.status_channel_id)
                if isinstance(channel, discord.TextChannel):
                    if self.matchmaker.captains_required(mode):
                        await channel.send(
                            f"**{mode.label}** on `{map_name}` has enough ready players. "
                            "Lobby players should click **Vote Captains** in this channel."
                        )
                    elif mode == MatchMode.ONE_V_ONE:
                        await channel.send(
                            f"**{mode.label}** has enough ready players. "
                            "Captains alternate **Ban Map**, then **Pick Side** (CT/T)."
                        )

        if match is not None:
            await self.announce_match(guild, match)

        return message, match

    async def _prepare_queue_member(
        self,
        member: discord.Member,
        guild: discord.Guild,
    ) -> tuple[MatchMode, str]:
        setup = await self._get_guild_setup(guild)
        if (
            setup is None
            or member.voice is None
            or member.voice.channel is None
            or not setup.is_queue_voice_channel(member.voice.channel.id)
        ):
            raise ValueError("Join a **Queue » 1v1 / 2v2 / 5v5** voice channel first.")

        steam_id, discord_name = await self._require_linked_player_id(member.id)
        mode = setup.mode_for_voice_channel(member.voice.channel.id)
        if mode is None:
            raise ValueError("Join a **Queue » 1v1 / 2v2 / 5v5** voice channel first.")

        if self.matchmaker.is_queued(member.id) is None:
            self.matchmaker.enter_queue(mode, member.id, discord_name, steam_id)

        queued = self.matchmaker.is_queued(member.id)
        if queued is None:
            raise ValueError("Join a **Queue » 1v1 / 2v2 / 5v5** voice channel first.")
        return queued

    async def _notify_premier_veto_started(
        self,
        guild: discord.Guild,
        mode: MatchMode,
        map_name: str,
        *,
        prefix: str = "Draft complete",
    ) -> None:
        veto_flow = self.matchmaker.get_premier_veto_flow(mode, map_name)
        if veto_flow.phase != PremierVetoPhase.BANNING:
            return

        setup = await self._get_guild_setup(guild)
        if setup is None:
            return

        channel = guild.get_channel(setup.status_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        captain_id = veto_flow.captain_for_team(veto_flow.ban_turn_team())
        captain_text = f"<@{captain_id}>" if captain_id is not None else "The captain"
        await channel.send(
            f"{prefix} for **{mode.label}** — Premier map veto started. "
            f"{captain_text} ({veto_flow.team_label(veto_flow.ban_turn_team())}), "
            "click **Ban Map**."
        )

    async def handle_open_premier_ban(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This button only works inside a server.",
                ephemeral=True,
            )
            return

        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "Could not resolve your member profile.",
                ephemeral=True,
            )
            return

        try:
            mode, map_name = await self._prepare_queue_member(member, interaction.guild)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        flow = self.matchmaker.get_premier_veto_flow(mode, map_name)
        if flow.phase == PremierVetoPhase.NONE and mode == MatchMode.ONE_V_ONE:
            self.matchmaker.maybe_start_premier_veto_1v1(mode, map_name)
        if flow.phase != PremierVetoPhase.BANNING:
            await interaction.response.send_message(
                "Premier map veto is not active for your queue right now.",
                ephemeral=True,
            )
            return
        if not flow.in_lobby(member.id):
            await interaction.response.send_message(
                "You are not in the active match lobby for this queue.",
                ephemeral=True,
            )
            return
        if member.id != flow.captain_for_team(flow.ban_turn_team()):
            await interaction.response.send_message(
                f"Only **{flow.team_label(flow.ban_turn_team())}** captain can ban right now.",
                ephemeral=True,
            )
            return

        remaining_maps = sorted(flow.remaining_maps)
        if not remaining_maps:
            await interaction.response.send_message(
                "No maps left to ban.",
                ephemeral=True,
            )
            return

        view = PremierBanSelectView(self, mode, map_name, remaining_maps)
        await interaction.response.send_message(
            f"**{flow.team_label(flow.ban_turn_team())}** — ban one map from the Active Duty pool.",
            view=view,
            ephemeral=True,
        )

    async def handle_open_side_pick(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This button only works inside a server.",
                ephemeral=True,
            )
            return

        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "Could not resolve your member profile.",
                ephemeral=True,
            )
            return

        try:
            mode, map_name = await self._prepare_queue_member(member, interaction.guild)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        flow = self.matchmaker.get_premier_veto_flow(mode, map_name)
        if flow.phase != PremierVetoPhase.SIDE_PICK:
            await interaction.response.send_message(
                "Side selection is not active for your queue right now.",
                ephemeral=True,
            )
            return
        if not flow.in_lobby(member.id):
            await interaction.response.send_message(
                "You are not in the active match lobby for this queue.",
                ephemeral=True,
            )
            return
        if flow.side_picker_team is None or member.id != flow.captain_for_team(flow.side_picker_team):
            picker = flow.team_label(flow.side_picker_team or CaptainTeam.ALPHA)
            await interaction.response.send_message(
                f"Only the **{picker}** captain can pick CT or T right now.",
                ephemeral=True,
            )
            return

        view = SidePickView(self, mode, map_name)
        await interaction.response.send_message(
            f"Pick your team's starting side on **{flow.chosen_map}**.",
            view=view,
            ephemeral=True,
        )

    async def handle_premier_ban(
        self,
        interaction: discord.Interaction,
        mode: MatchMode,
        map_name: str,
        banned_map_id: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("This control only works inside a server.", ephemeral=True)
            return

        try:
            mode, map_name, message, match = self.matchmaker.cast_premier_ban(
                interaction.user.id,
                banned_map_id,
            )
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        await self.refresh_queue_status(interaction.guild)
        await interaction.followup.send(message, ephemeral=True)

        if match is not None:
            await self.announce_match(interaction.guild, match)

    async def handle_premier_side(
        self,
        interaction: discord.Interaction,
        mode: MatchMode,
        map_name: str,
        side: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("This control only works inside a server.", ephemeral=True)
            return

        try:
            mode, map_name, message, match = self.matchmaker.cast_premier_side(
                interaction.user.id,
                side,
            )
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        await self.refresh_queue_status(interaction.guild)
        await interaction.followup.send(message, ephemeral=True)

        if match is not None:
            setup = await self._get_guild_setup(interaction.guild)
            if setup is not None:
                channel = interaction.guild.get_channel(setup.status_channel_id)
                if isinstance(channel, discord.TextChannel):
                    alpha_side = match.team1_side
                    alpha_label = "CT" if alpha_side == "ct" else "T"
                    bravo_label = "T" if alpha_side == "ct" else "CT"
                    await channel.send(
                        f"Premier veto complete for **{mode.label}** — `{match.map_name}` · "
                        f"Team Alpha **{alpha_label}**, Team Bravo **{bravo_label}**."
                    )
            await self.announce_match(interaction.guild, match)

    async def handle_open_captain_vote(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This button only works inside a server.",
                ephemeral=True,
            )
            return

        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "Could not resolve your member profile.",
                ephemeral=True,
            )
            return

        try:
            mode, map_name = await self._prepare_queue_member(member, interaction.guild)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        if not self.matchmaker.captains_required(mode):
            await interaction.response.send_message(
                "Captain voting is only used for 2v2 and 5v5 queues.",
                ephemeral=True,
            )
            return

        flow = self.matchmaker.get_captain_flow(mode, map_name)
        if flow.phase != CaptainPhase.VOTING:
            await interaction.response.send_message(
                "Captain voting is not active for your queue right now.",
                ephemeral=True,
            )
            return
        if not flow.in_lobby(member.id):
            await interaction.response.send_message(
                "You are not in the active match lobby for this queue.",
                ephemeral=True,
            )
            return

        candidates = self.matchmaker.get_lobby_candidates(mode, map_name)
        view = CaptainVoteSelectView(self, mode, map_name, candidates)
        await interaction.response.send_message(
            f"Vote for **{mode.label}** captains on `{map_name}`.",
            view=view,
            ephemeral=True,
        )

    async def handle_captain_vote(
        self,
        interaction: discord.Interaction,
        mode: MatchMode,
        map_name: str,
        team: str,
        candidate_id: int,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("This control only works inside a server.", ephemeral=True)
            return

        try:
            mode, map_name, message, match = self.matchmaker.cast_captain_vote(
                interaction.user.id,
                team,
                candidate_id,
            )
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        await self.refresh_queue_status(interaction.guild)
        await interaction.followup.send(message, ephemeral=True)

        if match is not None:
            await self.announce_match(interaction.guild, match)
            return

        await self._notify_premier_veto_started(interaction.guild, mode, map_name)
        if (
            self.matchmaker.get_premier_veto_flow(mode, map_name).phase
            == PremierVetoPhase.BANNING
        ):
            return

        flow = self.matchmaker.get_captain_flow(mode, map_name)
        if flow.phase == CaptainPhase.DRAFTING:
            setup = await self._get_guild_setup(interaction.guild)
            if setup is not None:
                channel = interaction.guild.get_channel(setup.status_channel_id)
                if isinstance(channel, discord.TextChannel):
                    picker = flow.current_picker_id()
                    picker_text = f"<@{picker}>" if picker is not None else "the captain"
                    await channel.send(
                        f"Captain voting finished for **{mode.label}** on `{map_name}`. "
                        f"{picker_text}, click **Pick Player** to draft your team."
                    )

    async def handle_open_draft_pick(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This button only works inside a server.",
                ephemeral=True,
            )
            return

        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "Could not resolve your member profile.",
                ephemeral=True,
            )
            return

        try:
            mode, map_name = await self._prepare_queue_member(member, interaction.guild)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        flow = self.matchmaker.get_captain_flow(mode, map_name)
        if flow.phase != CaptainPhase.DRAFTING:
            await interaction.response.send_message(
                "The player draft is not active for your queue right now.",
                ephemeral=True,
            )
            return
        if member.id != flow.current_picker_id():
            next_team = "Team Alpha" if flow.pick_turn == CaptainTeam.ALPHA else "Team Bravo"
            await interaction.response.send_message(
                f"It is not your turn to pick. Waiting on **{next_team}**.",
                ephemeral=True,
            )
            return

        candidates = self.matchmaker.get_draft_candidates(mode, map_name)
        if not candidates:
            await interaction.response.send_message(
                "There are no players left to pick.",
                ephemeral=True,
            )
            return

        view = CaptainPickSelectView(self, mode, map_name, candidates)
        await interaction.response.send_message(
            f"Pick a player for **{mode.label}** on `{map_name}`.",
            view=view,
            ephemeral=True,
        )

    async def handle_draft_pick(
        self,
        interaction: discord.Interaction,
        mode: MatchMode,
        map_name: str,
        picked_id: int,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("This control only works inside a server.", ephemeral=True)
            return

        try:
            mode, map_name, message, match = self.matchmaker.draft_pick(
                interaction.user.id,
                picked_id,
            )
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        await self.refresh_queue_status(interaction.guild)
        await interaction.followup.send(message, ephemeral=True)

        if match is not None:
            await self.announce_match(interaction.guild, match)
            return

        await self._notify_premier_veto_started(interaction.guild, mode, map_name)
        if (
            self.matchmaker.get_premier_veto_flow(mode, map_name).phase
            == PremierVetoPhase.BANNING
        ):
            return

        flow = self.matchmaker.get_captain_flow(mode, map_name)
        if flow.phase == CaptainPhase.DRAFTING:
            setup = await self._get_guild_setup(interaction.guild)
            if setup is not None:
                channel = interaction.guild.get_channel(setup.status_channel_id)
                if isinstance(channel, discord.TextChannel):
                    picker = flow.current_picker_id()
                    picker_text = f"<@{picker}>" if picker is not None else "the next captain"
                    next_team = "Team Alpha" if flow.pick_turn == CaptainTeam.ALPHA else "Team Bravo"
                    await channel.send(
                        f"**{next_team}** is up next — {picker_text}, click **Pick Player**."
                    )

    @commands.command(name="help")
    async def show_help(self, ctx: commands.Context) -> None:
        embed = discord.Embed(
            title="CS2 Match Bot — Help",
            description=(
                "Join a **Queue » 1v1 / 2v2 / 5v5** voice channel to enter the queue. "
                "Leave the channel to leave the queue.\n\n"
                f"React on **#{STATUS_CHANNEL_NAME}**:\n"
                f"{READY_EMOJI} — ready up\n"
                f"{UNREADY_EMOJI} — unready\n\n"
                "When a lobby is full, captains run **Premier map veto**: alternate **Ban Map** "
                "(Active Duty pool), then **Pick Side** (CT/T).\n"
                "For **2v2 / 5v5**, use **Vote Captains** and **Pick Player** first, then map veto.\n"
                "During matches, voice channels are **CT** and **T** based on the veto result.\n"
                "When a match ends, players are moved to **End Queue**.\n\n"
                f"**#{ELO_CHANNEL_NAME}** shows the live ELO leaderboard (top 10 per mode). "
                "Ratings reset every **3 months**. Wins add ELO, losses subtract ELO."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Player commands",
            value=(
                f"`{COMMAND_PREFIX}help` — this message\n"
                f"`{COMMAND_PREFIX}steamlink <steamID64>` — link your Steam account\n"
                f"`{COMMAND_PREFIX}profile show` — your linked Steam account and ELO\n"
                "**Link Steam Account** / **Unlink Steam** — buttons on `#queue-status`\n"
                f"`{COMMAND_PREFIX}profile leaderboard [1v1|2v2|5v5]` — top players by mode\n"
                f"`{COMMAND_PREFIX}queue status` — current queue sizes"
            ),
            inline=False,
        )
        embed.add_field(
            name="Admin commands",
            value=(
                f"`{COMMAND_PREFIX}admin setup` — create queue voice, End Queue, and status channels "
                "(admin role or **Manage Channels**)\n"
                f"All other `{COMMAND_PREFIX}admin` commands require the configured admin role "
                "or **Server Administrator**.\n"
                f"`{COMMAND_PREFIX}admin testserver` — test CS2 server connection\n"
                f"`{COMMAND_PREFIX}admin forcestart` — force-start the active MatchZy match\n"
                f"`{COMMAND_PREFIX}admin endmatch` — end the active match (no ELO change)\n"
                f"`{COMMAND_PREFIX}admin resetcaptains <1v1|2v2|5v5>` — restart captain draft / Premier veto\n"
                f"`{COMMAND_PREFIX}admin setcaptain <mode> <alpha|bravo> @player` — assign a captain"
            ),
            inline=False,
        )
        await ctx.send(embed=embed)

    @commands.group(name="queue", invoke_without_command=True)
    async def queue_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send(f"Use `{COMMAND_PREFIX}queue status`.")

    @queue_group.command(name="status")
    async def queue_status(self, ctx: commands.Context) -> None:
        await ctx.send(self.matchmaker.queue_summary())

    @commands.group(name="profile", invoke_without_command=True)
    async def profile_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send(
                f"Use `{COMMAND_PREFIX}profile show` or `{COMMAND_PREFIX}profile leaderboard [mode]`."
            )

    async def _save_steam_link(self, ctx: commands.Context, steam_value: str) -> None:
        success, message = await self.link_steam_account(
            ctx.author.id,
            ctx.author.display_name,
            steam_value,
        )
        if success:
            message = (
                f"{message}\n"
                "Join a **Queue » …** voice channel and react ✅ when ready."
            )
        await ctx.send(message)

    @commands.command(name="steamlink")
    async def steamlink(self, ctx: commands.Context, steam64: str) -> None:
        await self._save_steam_link(ctx, steam64)

    @profile_group.command(name="link")
    async def profile_link(self, ctx: commands.Context, steam_id: str) -> None:
        await self._save_steam_link(ctx, steam_id)

    @profile_group.command(name="show")
    async def profile_show(self, ctx: commands.Context) -> None:
        profile = await self.storage.get_player(ctx.author.id)
        if profile is None:
            await ctx.send(
                f"No Steam account linked yet. Use `{COMMAND_PREFIX}steamlink <steamID64>` "
                "or **Link Steam Account** on `#queue-status`."
            )
            return
        steam_id, discord_name = profile
        elo_profile = await self.elo_service.get_profile_elo(ctx.author.id)
        season = await self.elo_service.get_current_season()
        lines = [
            f"Linked Steam ID: `{steam_id}`",
            f"Stored name: `{discord_name}`",
            "",
            f"**Current season:** {season.label}",
            f"Next reset <t:{int(season.end.timestamp())}:R>",
            "",
            "**ELO ratings**",
        ]
        for mode in MatchMode:
            stats = elo_profile[mode]
            games = stats["wins"] + stats["losses"]
            lines.append(
                f"- {mode.label}: `{stats['rating']}` "
                f"({stats['wins']}W / {stats['losses']}L, {games} played)"
            )
        await ctx.send("\n".join(lines))

    @profile_group.command(name="leaderboard")
    async def profile_leaderboard(self, ctx: commands.Context, mode: str = "5v5") -> None:
        selected_mode = parse_match_mode(mode)
        season = await self.elo_service.get_current_season()
        rows = await self.elo_service.get_leaderboard(selected_mode, limit=10)
        if not rows:
            await ctx.send(
                f"No ELO data yet for {selected_mode.label} this season. Play a ranked match first!"
            )
            return

        lines = [
            f"**{selected_mode.label} Leaderboard**",
            f"Season: {season.label}",
        ]
        for index, row in enumerate(rows, start=1):
            lines.append(
                f"{index}. {row['discord_name']} — `{row['rating']}` "
                f"({row['wins']}W / {row['losses']}L)"
            )
        await ctx.send("\n".join(lines))

    @commands.group(name="admin", invoke_without_command=True)
    async def admin_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send(f"Use `{COMMAND_PREFIX}help` for admin command list.")

    @admin_group.command(name="setup")
    async def admin_setup(self, ctx: commands.Context) -> None:
        try:
            self._require_bot_admin(ctx, allow_manage_channels=True)
        except commands.CommandError as exc:
            await ctx.send(str(exc))
            return

        if ctx.guild is None:
            await ctx.send("This command must be used in a server.")
            return

        setup = await self.ensure_guild_channels(ctx.guild)
        await ctx.send(
            "Matchmaking channels are ready:\n"
            f"- <#{setup.status_channel_id}> (queue status + reactions)\n"
            f"- <#{setup.results_channel_id}> (recent match results)\n"
            f"- <#{setup.elo_channel_id}> (live ELO leaderboard)\n"
            f"- <#{setup.voice_channels[MatchMode.ONE_V_ONE]}> (1v1)\n"
            f"- <#{setup.voice_channels[MatchMode.TWO_V_TWO]}> (2v2)\n"
            f"- <#{setup.voice_channels[MatchMode.FIVE_V_FIVE]}> (5v5)\n"
            f"- <#{setup.end_queue_channel_id}> (End Queue — post-match lobby)"
        )

    @admin_group.command(name="testserver")
    async def admin_testserver(self, ctx: commands.Context) -> None:
        try:
            self._require_bot_admin(ctx)
        except commands.CommandError as exc:
            await ctx.send(str(exc))
            return

        if self.settings.server_provider == ServerProvider.DATHOST:
            from dathost_client import DatHostClient

            if not self.settings.dathost_game_server_id:
                await ctx.send("DATHOST_GAME_SERVER_ID is not configured.")
                return

            client = DatHostClient(
                email=self.settings.dathost_email or "",
                password=self.settings.dathost_password or "",
                server_id=self.settings.dathost_game_server_id,
                base_url=self.settings.dathost_api_base,
            )
            try:
                info = await client.get_server()
                await client.console_send("echo CS2 Match Bot connection test")
                await self.connect_resolver.refresh_dathost_connect_info()
            except Exception as exc:
                await ctx.send(f"DatHost connection failed: `{exc}`")
                return

            await ctx.send(
                f"DatHost server **{info.name}** is reachable.\n"
                f"- Online: `{info.online}`\n"
                f"- Connect: `{info.host}:{info.game_port}`\n"
                f"- Console test command sent successfully."
            )
            return

        try:
            response = await self.matchzy.console.execute("echo CS2 Match Bot connection test")
        except Exception as exc:
            await ctx.send(f"RCON connection failed: `{exc}`")
            return

        await ctx.send(
            f"RCON connection OK (`{self.settings.cs2_host}:{self.settings.cs2_port}`).\n"
            f"```{response or 'Command sent.'}```"
        )

    @admin_group.command(name="forcestart")
    async def admin_forcestart(self, ctx: commands.Context) -> None:
        try:
            self._require_bot_admin(ctx)
        except commands.CommandError as exc:
            await ctx.send(str(exc))
            return

        try:
            response = await self.matchzy.force_start()
        except Exception as exc:
            await ctx.send(f"{self._console_error_label()}: `{exc}`")
            return
        await ctx.send(f"Force start sent.\n```{response}```")

    @admin_group.command(name="resetcaptains")
    async def admin_resetcaptains(self, ctx: commands.Context, mode: str) -> None:
        try:
            self._require_bot_admin(ctx)
        except commands.CommandError as exc:
            await ctx.send(str(exc))
            return

        selected_mode = parse_match_mode(mode)
        try:
            messages: list[str] = []
            if self.matchmaker.captains_required(selected_mode):
                messages.append(
                    self.matchmaker.admin_reset_captains(
                        selected_mode,
                        self.settings.default_map,
                    )
                )
            messages.append(
                self.matchmaker.admin_reset_premier_veto(
                    selected_mode,
                    self.settings.default_map,
                )
            )
            message = " ".join(messages)
        except ValueError as exc:
            await ctx.send(str(exc))
            return

        if ctx.guild is not None:
            await self.refresh_queue_status(ctx.guild)
            if self.matchmaker.captains_required(selected_mode):
                self.matchmaker.maybe_start_captain_flow(
                    selected_mode,
                    self.settings.default_map,
                )
            elif selected_mode == MatchMode.ONE_V_ONE:
                self.matchmaker.maybe_start_premier_veto_1v1(
                    selected_mode,
                    self.settings.default_map,
                )
            setup = await self._get_guild_setup(ctx.guild)
            if setup is not None:
                channel = ctx.guild.get_channel(setup.status_channel_id)
                if isinstance(channel, discord.TextChannel):
                    await channel.send(
                        f"{ctx.author.mention} reset **{selected_mode.label}** lobby flow. "
                        "Ready players can start again."
                    )

        await ctx.send(message)

    @admin_group.command(name="setcaptain")
    async def admin_setcaptain(
        self,
        ctx: commands.Context,
        mode: str,
        team: str,
        player: discord.Member,
    ) -> None:
        try:
            self._require_bot_admin(ctx)
        except commands.CommandError as exc:
            await ctx.send(str(exc))
            return

        selected_mode = parse_match_mode(mode)
        if not self.matchmaker.captains_required(selected_mode):
            await ctx.send("Captain selection only applies to 2v2 and 5v5 queues.")
            return

        selected_team = parse_captain_team(team)
        try:
            message, match = self.matchmaker.admin_set_captain(
                selected_mode,
                player.id,
                selected_team,
                self.settings.default_map,
            )
        except ValueError as exc:
            await ctx.send(str(exc))
            return

        if ctx.guild is not None:
            await self.refresh_queue_status(ctx.guild)
            setup = await self._get_guild_setup(ctx.guild)
            if setup is not None:
                channel = ctx.guild.get_channel(setup.status_channel_id)
                if isinstance(channel, discord.TextChannel):
                    team_label = "Team Alpha" if selected_team == CaptainTeam.ALPHA else "Team Bravo"
                    await channel.send(
                        f"{ctx.author.mention} restarted captain vote/draft and assigned "
                        f"{player.mention} as **{team_label}** captain for **{selected_mode.label}**."
                    )
            if match is not None:
                await self.announce_match(ctx.guild, match)
            else:
                await self._notify_premier_veto_started(
                    ctx.guild,
                    selected_mode,
                    self.settings.default_map,
                    prefix="Captain draft complete",
                )

        await ctx.send(message)

    @admin_group.command(name="endmatch")
    async def admin_endmatch(self, ctx: commands.Context) -> None:
        try:
            self._require_bot_admin(ctx)
        except commands.CommandError as exc:
            await ctx.send(str(exc))
            return

        try:
            response = await self.matchzy.end_match()
        except Exception as exc:
            await ctx.send(f"{self._console_error_label()}: `{exc}`")
            return

        if ctx.guild is not None:
            active_ids = list(self.matchmaker.active_matches.keys())
            if not active_ids:
                active_ids = await self.storage.get_active_match_ids()
            for match_id in active_ids:
                await self.cleanup_match(ctx.guild, match_id, cancelled=True)

        await ctx.send(f"End match sent.\n```{response}```")

    async def _finish_match_from_event(
        self,
        guild: discord.Guild,
        match_id: str,
        payload: dict,
    ) -> None:
        self._cancel_match_finish_fallback(match_id)

        record = await self.storage.get_match_record(match_id)
        already_completed = record is not None and record.get("status") == "completed"

        if not already_completed:
            elo_changes = await self.elo_service.process_match_result(match_id, payload)
            await self._post_match_result(guild, match_id, payload, elo_changes)
            if elo_changes:
                await self.refresh_elo_leaderboard(guild)
                logger.info("Refreshed #elo-leaderboard after match %s", match_id)
        else:
            logger.info("Match %s already completed; skipping duplicate result post", match_id)

        await self.cleanup_match(guild, match_id)

    async def handle_match_event(self, event_name: str, payload: dict) -> None:
        event_name = normalize_event_name(str(payload.get("event", event_name)))
        match_id = extract_match_id(payload)
        logger.info("Match event %s for match %s", event_name, match_id)

        guild = await self._ensure_guild_for_events()
        if guild is None:
            return

        resolved_match_id = await self._resolve_match_id_for_event(
            match_id,
            allow_single_active_fallback=event_name in LIVE_UPDATE_EVENTS,
        )

        if resolved_match_id is not None and event_name in LIVE_UPDATE_EVENTS:
            await self._update_live_match_embed(guild, resolved_match_id, event_name, payload)

        if event_name == "map_result" and resolved_match_id is not None:
            self._schedule_match_finish_fallback(guild, resolved_match_id, payload)
            return

        if event_name not in FINISH_EVENTS:
            return

        if resolved_match_id is None:
            resolved_match_id = await self._resolve_match_id_for_event(
                match_id,
                allow_single_active_fallback=True,
            )

        if resolved_match_id is not None:
            await self._finish_match_from_event(guild, resolved_match_id, payload)
            return

        active_ids = await self._active_match_ids()
        logger.error(
            "Ignoring finish event %s with unresolved match id %r; active matches: %s",
            event_name,
            match_id,
            active_ids,
        )
