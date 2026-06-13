from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from config import ServerProvider, Settings
from dathost_client import DatHostClient
from rcon import RconClient


class ServerConsole(Protocol):
    async def execute(self, command: str) -> str: ...


@dataclass
class RconConsole:
    client: RconClient

    async def execute(self, command: str) -> str:
        return await self.client.execute(command)


@dataclass
class DatHostConsole:
    client: DatHostClient

    async def execute(self, command: str) -> str:
        await self.client.console_send(command)
        return f"Sent to DatHost console: {command}"


def create_server_console(settings: Settings) -> ServerConsole:
    if settings.server_provider == ServerProvider.DATHOST:
        if not settings.dathost_email or not settings.dathost_password:
            raise RuntimeError(
                "DATHOST_EMAIL and DATHOST_PASSWORD are required when CS2_SERVER_PROVIDER=dathost"
            )
        if not settings.dathost_game_server_id:
            raise RuntimeError(
                "DATHOST_GAME_SERVER_ID is required when CS2_SERVER_PROVIDER=dathost"
            )
        return DatHostConsole(
            DatHostClient(
                email=settings.dathost_email,
                password=settings.dathost_password,
                server_id=settings.dathost_game_server_id,
                base_url=settings.dathost_api_base,
            )
        )

    return RconConsole(
        RconClient(
            host=settings.cs2_host,
            port=settings.cs2_port,
            password=settings.cs2_rcon_password,
        )
    )
