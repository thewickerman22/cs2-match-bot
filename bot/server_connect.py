from __future__ import annotations

import logging

from config import ServerProvider, Settings
from dathost_client import DatHostClient

logger = logging.getLogger(__name__)


class ServerConnectResolver:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cached_host: str | None = None
        self._cached_port: int | None = None

    async def refresh_dathost_connect_info(self) -> None:
        if self.settings.server_provider != ServerProvider.DATHOST:
            return
        if not self.settings.dathost_email or not self.settings.dathost_password:
            return
        if not self.settings.dathost_game_server_id:
            return

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
            return

        if info.host:
            self._cached_host = info.host
            self._cached_port = info.game_port
            logger.info(
                "DatHost server connect info: %s:%s (online=%s)",
                info.host,
                info.game_port,
                info.online,
            )

    def get_connect_host(self) -> str:
        if self.settings.cs2_public_host:
            return self.settings.cs2_public_host
        if self._cached_host:
            return self._cached_host
        if self.settings.server_provider == ServerProvider.DATHOST:
            return self.settings.cs2_host
        return self.settings.cs2_host

    def get_connect_port(self) -> int:
        if self.settings.cs2_public_port is not None:
            return self.settings.cs2_public_port
        if self._cached_port is not None:
            return self._cached_port
        return self.settings.cs2_port

    def get_connect_password(self) -> str | None:
        return self.settings.cs2_password
