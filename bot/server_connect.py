from __future__ import annotations

import logging
import time

from config import ServerProvider, Settings
from dathost_client import DatHostClient, DatHostServerInfo
from server_query import query_a2s, wait_until_a2s_responsive

logger = logging.getLogger(__name__)

_INVALID_CONNECT_HOSTS = frozenset({"", "cs2-server", "127.0.0.1", "localhost"})


class ServerConnectResolver:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cached_host: str | None = None
        self._cached_ip: str | None = None
        self._cached_custom_domain: str | None = None
        self._cached_port: int | None = None
        self._cached_password: str | None = None
        self._server_ready: bool = False
        self._udp_ready: bool = True
        self._last_refresh_at: float = 0.0

    def mark_match_deploy_started(self) -> None:
        """Map reload during MatchZy deploy closes the game port temporarily."""
        self._server_ready = False
        self._udp_ready = False

    def mark_udp_ready(self) -> None:
        self._udp_ready = True
        self._server_ready = True

    def apply_server_info(self, info: DatHostServerInfo) -> None:
        if info.host:
            self._cached_host = info.host
            self._cached_port = info.game_port
        if info.ip:
            self._cached_ip = info.ip
        if info.custom_domain:
            self._cached_custom_domain = info.custom_domain
        if info.game_password is not None:
            self._cached_password = info.game_password
        self._server_ready = info.is_ready_for_players
        self._last_refresh_at = time.monotonic()

    async def refresh_dathost_connect_info(
        self,
        *,
        force: bool = False,
        min_interval: int | None = None,
    ) -> DatHostServerInfo | None:
        if self.settings.server_provider != ServerProvider.DATHOST:
            return None
        if not self.settings.dathost_email or not self.settings.dathost_password:
            return None
        if not self.settings.dathost_game_server_id:
            return None

        if min_interval is None:
            min_interval = self.settings.dathost_connect_refresh_seconds
        if (
            not force
            and min_interval > 0
            and self._last_refresh_at
            and time.monotonic() - self._last_refresh_at < min_interval
        ):
            return None

        client = DatHostClient(
            email=self.settings.dathost_email,
            password=self.settings.dathost_password,
            server_id=self.settings.dathost_game_server_id,
            base_url=self.settings.dathost_api_base,
        )
        try:
            info = await client.get_server(prefer_ip=self.settings.cs2_connect_prefer_ip)
        except Exception:
            logger.exception("Failed to fetch DatHost server connection info")
            return None

        self.apply_server_info(info)
        logger.info(
            "DatHost server connect info: %s:%s (on=%s, booting=%s, password=%s, ip=%s, domain=%s)",
            info.host or "?",
            info.game_port,
            info.online,
            info.booting,
            "set" if self.get_connect_password() else "none",
            info.ip or "?",
            info.custom_domain or "?",
        )
        return info

    async def wait_for_game_port(
        self,
        *,
        timeout_seconds: int | None = None,
        poll_interval: int | None = None,
    ) -> bool:
        if timeout_seconds is None:
            timeout_seconds = self.settings.dathost_udp_ready_timeout_seconds
        if poll_interval is None:
            poll_interval = self.settings.dathost_udp_poll_seconds

        host = self.get_connect_host()
        port = self.get_connect_port()
        ready = await wait_until_a2s_responsive(
            host,
            port,
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval,
        )
        if ready:
            self.mark_udp_ready()
        return ready

    async def probe_game_port(self) -> bool:
        return await query_a2s(self.get_connect_host(), self.get_connect_port())

    def get_connect_host(self) -> str:
        if self.settings.cs2_public_host:
            return self.settings.cs2_public_host.strip()
        if self._cached_host:
            return self._cached_host
        return self.settings.cs2_host

    def get_connect_alternate_host(self) -> str | None:
        host = self.get_connect_host()
        if self._cached_ip and self._cached_ip != host:
            return self._cached_ip
        if self._cached_custom_domain and self._cached_custom_domain != host:
            return self._cached_custom_domain
        return None

    def get_connect_port(self) -> int:
        if self.settings.cs2_public_port is not None:
            return self.settings.cs2_public_port
        if self._cached_port is not None:
            return self._cached_port
        return self.settings.cs2_port

    def get_connect_password(self) -> str | None:
        if self.settings.cs2_password:
            return self.settings.cs2_password
        return self._cached_password

    def is_connect_ready(self) -> bool:
        host = self.get_connect_host().strip().lower()
        if host in _INVALID_CONNECT_HOSTS:
            return False
        if self.settings.server_provider == ServerProvider.DATHOST:
            return self._server_ready and self._udp_ready
        return True
