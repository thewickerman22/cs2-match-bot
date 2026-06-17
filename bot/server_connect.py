from __future__ import annotations

import logging

from config import ServerProvider, Settings
from dathost_client import DatHostClient, DatHostServerInfo

logger = logging.getLogger(__name__)

_INVALID_CONNECT_HOSTS = frozenset({"", "cs2-server", "127.0.0.1", "localhost"})


class ServerConnectResolver:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cached_host: str | None = None
        self._cached_port: int | None = None
        self._cached_password: str | None = None
        self._server_ready: bool = False

    def apply_server_info(self, info: DatHostServerInfo) -> None:
        if info.host:
            self._cached_host = info.host
            self._cached_port = info.game_port
        if info.game_password is not None:
            self._cached_password = info.game_password
        self._server_ready = info.is_ready_for_players

    async def refresh_dathost_connect_info(self) -> DatHostServerInfo | None:
        if self.settings.server_provider != ServerProvider.DATHOST:
            return None
        if not self.settings.dathost_email or not self.settings.dathost_password:
            return None
        if not self.settings.dathost_game_server_id:
            return None

        client = DatHostClient(
            email=self.settings.dathost_email,
            password=self.settings.dathost_password,
            server_id=self.settings.dathost_game_server_id,
            base_url=self.settings.dathost_api_base,
        )
        try:
            info = await client.get_server()
        except Exception:
            logger.exception("Failed to fetch DatHost server connection info")
            return None

        self.apply_server_info(info)
        logger.info(
            "DatHost server connect info: %s:%s (on=%s, booting=%s, password=%s)",
            info.host or "?",
            info.game_port,
            info.online,
            info.booting,
            "set" if self.get_connect_password() else "none",
        )
        return info

    def get_connect_host(self) -> str:
        if self.settings.cs2_public_host:
            return self.settings.cs2_public_host.strip()
        if self._cached_host:
            return self._cached_host
        return self.settings.cs2_host

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
            return self._server_ready
        return True
