from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class ServerProvider(str, Enum):
    LOCAL = "local"
    DATHOST = "dathost"


class MatchMode(str, Enum):
    ONE_V_ONE = "1v1"
    TWO_V_TWO = "2v2"
    FIVE_V_FIVE = "5v5"

    @property
    def players_per_team(self) -> int:
        return {
            MatchMode.ONE_V_ONE: 1,
            MatchMode.TWO_V_TWO: 2,
            MatchMode.FIVE_V_FIVE: 5,
        }[self]

    @property
    def total_players(self) -> int:
        return self.players_per_team * 2

    @property
    def label(self) -> str:
        return {
            MatchMode.ONE_V_ONE: "1v1",
            MatchMode.TWO_V_TWO: "2v2 Wingman",
            MatchMode.FIVE_V_FIVE: "5v5",
        }[self]


@dataclass(frozen=True)
class Settings:
    discord_token: str
    discord_guild_id: int | None
    http_host: str
    http_port: int
    public_url: str
    cs2_host: str
    cs2_port: int
    cs2_rcon_password: str
    default_map: str
    matchzy_api_key: str
    database_path: str
    default_elo: int
    k_factor: int
    discord_admin_role_id: int | None
    server_provider: ServerProvider
    dathost_email: str | None
    dathost_password: str | None
    dathost_game_server_id: str | None
    dathost_api_base: str
    cs2_public_host: str | None
    cs2_public_port: int | None
    cs2_password: str | None
    queue_ready_timeout_seconds: int
    map_result_finish_fallback_seconds: int
    match_status_poll_seconds: int
    queue_status_refresh_seconds: int
    transient_message_seconds: int
    match_results_retain_count: int


def load_settings() -> Settings:
    guild_raw = os.getenv("DISCORD_GUILD_ID", "").strip()
    admin_role_raw = os.getenv("DISCORD_ADMIN_ROLE_ID", "").strip()
    provider_raw = os.getenv("CS2_SERVER_PROVIDER", "local").strip().lower()
    public_port_raw = os.getenv("CS2_PUBLIC_PORT", "").strip()

    try:
        server_provider = ServerProvider(provider_raw)
    except ValueError as exc:
        allowed = ", ".join(provider.value for provider in ServerProvider)
        raise RuntimeError(
            f"Invalid CS2_SERVER_PROVIDER '{provider_raw}'. Use one of: {allowed}"
        ) from exc

    return Settings(
        discord_token=os.environ["DISCORD_TOKEN"],
        discord_guild_id=int(guild_raw) if guild_raw else None,
        http_host=os.getenv("BOT_HTTP_HOST", "0.0.0.0"),
        http_port=int(os.getenv("BOT_HTTP_PORT", "8080")),
        public_url=os.getenv("BOT_PUBLIC_URL", "http://localhost:8080").rstrip("/"),
        cs2_host=os.getenv("CS2_HOST", "127.0.0.1"),
        cs2_port=int(os.getenv("CS2_PORT", "27015")),
        cs2_rcon_password=os.getenv("CS2_RCON_PASSWORD", "changeme"),
        default_map=os.getenv("DEFAULT_MAP", "de_dust2"),
        matchzy_api_key=os.getenv("MATCHZY_API_KEY", "change-me"),
        database_path=os.getenv("DATABASE_PATH", "/app/data/bot.sqlite3"),
        default_elo=int(os.getenv("ELO_DEFAULT", "1000")),
        k_factor=int(os.getenv("ELO_K_FACTOR", "32")),
        discord_admin_role_id=int(admin_role_raw) if admin_role_raw else None,
        server_provider=server_provider,
        dathost_email=os.getenv("DATHOST_EMAIL", "").strip() or None,
        dathost_password=os.getenv("DATHOST_PASSWORD", "").strip() or None,
        dathost_game_server_id=os.getenv("DATHOST_GAME_SERVER_ID", "").strip() or None,
        dathost_api_base=os.getenv("DATHOST_API_BASE", "https://dathost.net/api/0.1").rstrip("/"),
        cs2_public_host=os.getenv("CS2_PUBLIC_HOST", "").strip() or None,
        cs2_public_port=int(public_port_raw) if public_port_raw else None,
        cs2_password=os.getenv("CS2_PW", "").strip() or None,
        queue_ready_timeout_seconds=int(os.getenv("QUEUE_READY_TIMEOUT_SECONDS", "300")),
        map_result_finish_fallback_seconds=int(
            os.getenv("MAP_RESULT_FINISH_FALLBACK_SECONDS", "20")
        ),
        match_status_poll_seconds=int(os.getenv("MATCH_STATUS_POLL_SECONDS", "45")),
        queue_status_refresh_seconds=int(os.getenv("QUEUE_STATUS_REFRESH_SECONDS", "15")),
        transient_message_seconds=int(os.getenv("TRANSIENT_MESSAGE_SECONDS", "5")),
        match_results_retain_count=int(os.getenv("MATCH_RESULTS_RETAIN_COUNT", "5")),
    )
