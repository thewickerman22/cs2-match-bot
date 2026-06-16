from __future__ import annotations

import json

import aiosqlite

from elo import EloChange


NEXT_MATCH_ID_KEY = "next_match_id"


class Storage:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path

    async def initialize(self) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS players (
                    discord_id INTEGER PRIMARY KEY,
                    steam_id TEXT NOT NULL UNIQUE,
                    discord_name TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS matches (
                    match_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    map_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id INTEGER PRIMARY KEY,
                    category_id INTEGER NOT NULL,
                    status_channel_id INTEGER NOT NULL,
                    status_message_id INTEGER,
                    voice_1v1_id INTEGER NOT NULL,
                    voice_2v2_id INTEGER NOT NULL,
                    voice_5v5_id INTEGER NOT NULL
                );
                """
            )
            await db.commit()
            await self._migrate_schema(db)

    async def _migrate_schema(self, db: aiosqlite.Connection) -> None:
        cursor = await db.execute("PRAGMA table_info(matches)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "team1_voice_id" not in columns:
            await db.execute("ALTER TABLE matches ADD COLUMN team1_voice_id INTEGER")
        if "team2_voice_id" not in columns:
            await db.execute("ALTER TABLE matches ADD COLUMN team2_voice_id INTEGER")
        if "live_results_message_id" not in columns:
            await db.execute("ALTER TABLE matches ADD COLUMN live_results_message_id INTEGER")
        await db.commit()

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS match_voice_returns (
                match_id TEXT NOT NULL,
                discord_id INTEGER NOT NULL,
                original_channel_id INTEGER NOT NULL,
                PRIMARY KEY (match_id, discord_id)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS player_elo (
                discord_id INTEGER NOT NULL,
                mode TEXT NOT NULL,
                rating INTEGER NOT NULL DEFAULT 1000,
                wins INTEGER NOT NULL DEFAULT 0,
                losses INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (discord_id, mode)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS match_elo_processed (
                match_id TEXT PRIMARY KEY
            )
            """
        )
        await db.commit()

        if "roster_json" not in columns:
            await db.execute("ALTER TABLE matches ADD COLUMN roster_json TEXT")
            await db.commit()

        cursor = await db.execute("PRAGMA table_info(guild_settings)")
        guild_columns = {row[1] for row in await cursor.fetchall()}
        if "results_channel_id" not in guild_columns:
            await db.execute("ALTER TABLE guild_settings ADD COLUMN results_channel_id INTEGER")
            await db.commit()

        if "elo_channel_id" not in guild_columns:
            await db.execute("ALTER TABLE guild_settings ADD COLUMN elo_channel_id INTEGER")
            await db.commit()
        if "elo_message_id" not in guild_columns:
            await db.execute("ALTER TABLE guild_settings ADD COLUMN elo_message_id INTEGER")
            await db.commit()
        if "end_queue_channel_id" not in guild_columns:
            await db.execute("ALTER TABLE guild_settings ADD COLUMN end_queue_channel_id INTEGER")
            await db.commit()
        if "commands_channel_id" not in guild_columns:
            await db.execute("ALTER TABLE guild_settings ADD COLUMN commands_channel_id INTEGER")
            await db.commit()
        if "commands_player_message_id" not in guild_columns:
            await db.execute(
                "ALTER TABLE guild_settings ADD COLUMN commands_player_message_id INTEGER"
            )
            await db.commit()
        if "commands_admin_message_id" not in guild_columns:
            await db.execute(
                "ALTER TABLE guild_settings ADD COLUMN commands_admin_message_id INTEGER"
            )
            await db.commit()

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_result_messages (
                guild_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                match_id TEXT NOT NULL,
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, message_id)
            )
            """
        )
        await db.commit()

    async def get_guild_setup(self, guild_id: int):
        from guild_setup import GuildSetup
        from config import MatchMode

        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                """
                SELECT category_id, status_channel_id, status_message_id,
                       voice_1v1_id, voice_2v2_id, voice_5v5_id, results_channel_id,
                       elo_channel_id, elo_message_id, end_queue_channel_id,
                       commands_channel_id, commands_player_message_id,
                       commands_admin_message_id
                FROM guild_settings
                WHERE guild_id = ?
                """,
                (guild_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            results_channel_id = row[6] if row[6] is not None else 0
            elo_channel_id = row[7] if row[7] is not None else 0
            end_queue_channel_id = row[9] if len(row) > 9 and row[9] is not None else 0
            commands_channel_id = row[10] if len(row) > 10 and row[10] is not None else 0
            commands_player_message_id = row[11] if len(row) > 11 else None
            commands_admin_message_id = row[12] if len(row) > 12 else None
            return GuildSetup(
                guild_id=guild_id,
                category_id=row[0],
                status_channel_id=row[1],
                status_message_id=row[2],
                results_channel_id=results_channel_id,
                elo_channel_id=elo_channel_id,
                elo_message_id=row[8],
                voice_channels={
                    MatchMode.ONE_V_ONE: row[3],
                    MatchMode.TWO_V_TWO: row[4],
                    MatchMode.FIVE_V_FIVE: row[5],
                },
                end_queue_channel_id=end_queue_channel_id,
                commands_channel_id=commands_channel_id,
                commands_player_message_id=commands_player_message_id,
                commands_admin_message_id=commands_admin_message_id,
            )

    async def save_guild_setup(self, setup) -> None:
        from config import MatchMode

        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO guild_settings (
                    guild_id, category_id, status_channel_id, status_message_id,
                    voice_1v1_id, voice_2v2_id, voice_5v5_id, results_channel_id,
                    elo_channel_id, elo_message_id, end_queue_channel_id,
                    commands_channel_id, commands_player_message_id,
                    commands_admin_message_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    category_id = excluded.category_id,
                    status_channel_id = excluded.status_channel_id,
                    status_message_id = excluded.status_message_id,
                    voice_1v1_id = excluded.voice_1v1_id,
                    voice_2v2_id = excluded.voice_2v2_id,
                    voice_5v5_id = excluded.voice_5v5_id,
                    results_channel_id = excluded.results_channel_id,
                    elo_channel_id = excluded.elo_channel_id,
                    elo_message_id = excluded.elo_message_id,
                    end_queue_channel_id = excluded.end_queue_channel_id,
                    commands_channel_id = excluded.commands_channel_id,
                    commands_player_message_id = excluded.commands_player_message_id,
                    commands_admin_message_id = excluded.commands_admin_message_id
                """,
                (
                    setup.guild_id,
                    setup.category_id,
                    setup.status_channel_id,
                    setup.status_message_id,
                    setup.voice_channels[MatchMode.ONE_V_ONE],
                    setup.voice_channels[MatchMode.TWO_V_TWO],
                    setup.voice_channels[MatchMode.FIVE_V_FIVE],
                    setup.results_channel_id,
                    setup.elo_channel_id,
                    setup.elo_message_id,
                    setup.end_queue_channel_id,
                    setup.commands_channel_id,
                    setup.commands_player_message_id,
                    setup.commands_admin_message_id,
                ),
            )
            await db.commit()

    async def update_status_message_id(self, guild_id: int, message_id: int) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                "UPDATE guild_settings SET status_message_id = ? WHERE guild_id = ?",
                (message_id, guild_id),
            )
            await db.commit()

    async def update_elo_message_id(self, guild_id: int, message_id: int | None) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                "UPDATE guild_settings SET elo_message_id = ? WHERE guild_id = ?",
                (message_id, guild_id),
            )
            await db.commit()

    async def get_bot_meta(self, key: str) -> str | None:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                "SELECT value FROM bot_meta WHERE key = ?",
                (key,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return row[0]

    async def set_bot_meta(self, key: str, value: str) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO bot_meta (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            await db.commit()

    async def initialize_match_id_counter(self) -> int:
        raw = await self.get_bot_meta(NEXT_MATCH_ID_KEY)
        if raw is not None and raw.isdigit():
            return max(1, int(raw))

        max_numeric = 0
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute("SELECT match_id FROM matches")
            for (match_id,) in await cursor.fetchall():
                if str(match_id).isdigit():
                    max_numeric = max(max_numeric, int(match_id))
        return max_numeric + 1

    async def set_next_match_id(self, next_id: int) -> None:
        await self.set_bot_meta(NEXT_MATCH_ID_KEY, str(max(1, next_id)))

    async def reset_all_player_elo(self) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute("DELETE FROM player_elo")
            await db.commit()

    async def upsert_player(self, discord_id: int, steam_id: str, discord_name: str) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO players (discord_id, steam_id, discord_name)
                VALUES (?, ?, ?)
                ON CONFLICT(discord_id) DO UPDATE SET
                    steam_id = excluded.steam_id,
                    discord_name = excluded.discord_name,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (discord_id, steam_id, discord_name),
            )
            await db.commit()

    async def get_player(self, discord_id: int) -> tuple[str, str] | None:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                "SELECT steam_id, discord_name FROM players WHERE discord_id = ?",
                (discord_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return row[0], row[1]

    async def delete_player(self, discord_id: int) -> bool:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                "DELETE FROM players WHERE discord_id = ?",
                (discord_id,),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def save_match(self, match_id: str, mode: str, map_name: str, payload_json: str) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO matches (match_id, mode, map_name, status, payload_json)
                VALUES (?, ?, ?, 'active', ?)
                """,
                (match_id, mode, map_name, payload_json),
            )
            await db.commit()

    async def save_match_voice_channels(
        self,
        match_id: str,
        team1_voice_id: int,
        team2_voice_id: int,
    ) -> bool:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                """
                UPDATE matches
                SET team1_voice_id = ?, team2_voice_id = ?
                WHERE match_id = ?
                """,
                (team1_voice_id, team2_voice_id, match_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def clear_match_voice_channels(self, match_id: str) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                UPDATE matches
                SET team1_voice_id = NULL, team2_voice_id = NULL
                WHERE match_id = ?
                """,
                (match_id,),
            )
            await db.commit()

    async def get_match_voice_channels(self, match_id: str) -> tuple[int, int] | None:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                """
                SELECT team1_voice_id, team2_voice_id
                FROM matches
                WHERE match_id = ?
                """,
                (match_id,),
            )
            row = await cursor.fetchone()
            if row is None or row[0] is None or row[1] is None:
                return None
            return row[0], row[1]

    async def save_live_results_message_id(self, match_id: str, message_id: int) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                UPDATE matches
                SET live_results_message_id = ?
                WHERE match_id = ?
                """,
                (message_id, match_id),
            )
            await db.commit()

    async def get_live_results_message_id(self, match_id: str) -> int | None:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                """
                SELECT live_results_message_id
                FROM matches
                WHERE match_id = ?
                """,
                (match_id,),
            )
            row = await cursor.fetchone()
            if row is None or row[0] is None:
                return None
            return int(row[0])

    async def clear_live_results_message_id(self, match_id: str) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                UPDATE matches
                SET live_results_message_id = NULL
                WHERE match_id = ?
                """,
                (match_id,),
            )
            await db.commit()

    async def add_guild_result_message(
        self,
        guild_id: int,
        message_id: int,
        match_id: str,
    ) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO guild_result_messages (guild_id, message_id, match_id)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, message_id) DO UPDATE SET
                    match_id = excluded.match_id,
                    recorded_at = CURRENT_TIMESTAMP
                """,
                (guild_id, message_id, match_id),
            )
            await db.commit()

    async def list_guild_result_messages(
        self,
        guild_id: int,
        *,
        offset: int = 0,
    ) -> list[tuple[int, str]]:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                """
                SELECT message_id, match_id
                FROM guild_result_messages
                WHERE guild_id = ?
                ORDER BY recorded_at DESC, message_id DESC
                LIMIT -1 OFFSET ?
                """,
                (guild_id, offset),
            )
            rows = await cursor.fetchall()
            return [(int(message_id), str(match_id)) for message_id, match_id in rows]

    async def remove_guild_result_message(self, guild_id: int, message_id: int) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                DELETE FROM guild_result_messages
                WHERE guild_id = ? AND message_id = ?
                """,
                (guild_id, message_id),
            )
            await db.commit()

    async def save_match_roster(self, match_id: str, roster: dict) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                "UPDATE matches SET roster_json = ? WHERE match_id = ?",
                (json.dumps(roster), match_id),
            )
            await db.commit()

    async def match_exists(self, match_id: str) -> bool:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM matches WHERE match_id = ?",
                (match_id,),
            )
            return await cursor.fetchone() is not None

    async def get_match_record(self, match_id: str) -> dict | None:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                """
                SELECT mode, map_name, roster_json, status
                FROM matches
                WHERE match_id = ?
                """,
                (match_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            roster = json.loads(row[2]) if row[2] else None
            return {
                "mode": row[0],
                "map_name": row[1],
                "roster": roster,
                "status": row[3],
            }

    async def is_elo_processed(self, match_id: str) -> bool:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM match_elo_processed WHERE match_id = ?",
                (match_id,),
            )
            return await cursor.fetchone() is not None

    async def mark_elo_processed(self, match_id: str) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO match_elo_processed (match_id) VALUES (?)",
                (match_id,),
            )
            await db.commit()

    async def get_player_ratings(
        self,
        discord_ids: list[int],
        mode: str,
        default_elo: int = 1000,
    ) -> dict[int, int]:
        if not discord_ids:
            return {}
        placeholders = ",".join("?" for _ in discord_ids)
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                f"""
                SELECT discord_id, rating
                FROM player_elo
                WHERE mode = ? AND discord_id IN ({placeholders})
                """,
                (mode, *discord_ids),
            )
            rows = await cursor.fetchall()
            ratings = {discord_id: default_elo for discord_id in discord_ids}
            for discord_id, rating in rows:
                ratings[discord_id] = rating
            return ratings

    async def apply_elo_changes(self, mode: str, changes: list[EloChange]) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            for change in changes:
                await db.execute(
                    """
                    INSERT INTO player_elo (discord_id, mode, rating, wins, losses)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(discord_id, mode) DO UPDATE SET
                        rating = excluded.rating,
                        wins = player_elo.wins + excluded.wins,
                        losses = player_elo.losses + excluded.losses
                    """,
                    (
                        change.discord_id,
                        mode,
                        change.new_rating,
                        1 if change.won else 0,
                        0 if change.won else 1,
                    ),
                )
            await db.commit()

    async def get_all_player_elo(self, discord_id: int) -> dict[str, dict[str, int]]:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                """
                SELECT mode, rating, wins, losses
                FROM player_elo
                WHERE discord_id = ?
                """,
                (discord_id,),
            )
            rows = await cursor.fetchall()
            return {
                mode: {"rating": rating, "wins": wins, "losses": losses}
                for mode, rating, wins, losses in rows
            }

    async def get_leaderboard(
        self,
        mode: str,
        limit: int,
        default_elo: int,
    ) -> list[dict]:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                """
                SELECT p.discord_name, e.rating, e.wins, e.losses, p.discord_id
                FROM player_elo e
                JOIN players p ON p.discord_id = e.discord_id
                WHERE e.mode = ?
                ORDER BY e.rating DESC, e.wins DESC
                LIMIT ?
                """,
                (mode, limit),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "discord_name": row[0],
                    "rating": row[1],
                    "wins": row[2],
                    "losses": row[3],
                    "discord_id": row[4],
                }
                for row in rows
            ]

    async def get_active_match_ids(self) -> list[str]:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                "SELECT match_id FROM matches WHERE status = 'active'"
            )
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

    async def get_match_payload_json(self, match_id: str) -> str | None:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                """
                SELECT payload_json
                FROM matches
                WHERE match_id = ? AND status = 'active'
                """,
                (match_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return row[0]

    async def update_match_status(self, match_id: str, status: str) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                "UPDATE matches SET status = ? WHERE match_id = ?",
                (status, match_id),
            )
            await db.commit()
