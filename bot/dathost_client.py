from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DatHostServerInfo:
    server_id: str
    host: str
    game_port: int
    name: str
    online: bool


class DatHostClient:
    def __init__(
        self,
        email: str,
        password: str,
        server_id: str,
        base_url: str = "https://dathost.net/api/0.1",
        timeout: float = 30.0,
    ) -> None:
        self.email = email
        self.password = password
        self.server_id = server_id
        self.base_url = base_url.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._auth = aiohttp.BasicAuth(email, password)

    async def get_server(self) -> DatHostServerInfo:
        url = f"{self.base_url}/game-servers/{self.server_id}"
        async with aiohttp.ClientSession(timeout=self.timeout, auth=self._auth) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                payload: dict[str, Any] = await response.json()

        ports = payload.get("ports") or {}
        game_port = int(ports.get("game") or ports.get("game_port") or 27015)
        return DatHostServerInfo(
            server_id=self.server_id,
            host=str(payload.get("ip") or ""),
            game_port=game_port,
            name=str(payload.get("name") or "DatHost CS2 Server"),
            online=bool(payload.get("on") or payload.get("online")),
        )

    async def console_send(self, line: str) -> None:
        command = line.strip()
        if not command:
            raise ValueError("Console command cannot be empty")

        url = f"{self.base_url}/game-servers/{self.server_id}/console"
        form = aiohttp.FormData()
        form.add_field("line", command)
        async with aiohttp.ClientSession(timeout=self.timeout, auth=self._auth) as session:
            async with session.post(url, data=form) as response:
                if response.status >= 400:
                    body = await response.text()
                    raise RuntimeError(
                        f"DatHost console command failed ({response.status}): {body}"
                    )

    async def start_server(self) -> None:
        url = f"{self.base_url}/game-servers/{self.server_id}/start"
        async with aiohttp.ClientSession(timeout=self.timeout, auth=self._auth) as session:
            async with session.post(url) as response:
                if response.status >= 400:
                    body = await response.text()
                    raise RuntimeError(
                        f"DatHost server start failed ({response.status}): {body}"
                    )
