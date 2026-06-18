from __future__ import annotations

import asyncio
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
    booting: bool = False
    game_password: str | None = None
    ip: str | None = None
    custom_domain: str | None = None
    private_server: bool = False

    @property
    def is_ready_for_players(self) -> bool:
        return self.online and not self.booting


def _extract_game_settings(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("cs2_settings", "csgo_settings"):
        settings = payload.get(key)
        if isinstance(settings, dict):
            return settings
    return {}


def _extract_game_password(payload: dict[str, Any]) -> str | None:
    settings = _extract_game_settings(payload)
    password = settings.get("password")
    if password is not None and str(password).strip():
        return str(password).strip()
    return None


def _extract_connect_endpoints(payload: dict[str, Any], *, prefer_ip: bool) -> tuple[str, str | None, str | None]:
    ip = str(payload.get("ip") or "").strip() or None
    custom_domain = str(payload.get("custom_domain") or "").strip() or None
    fallback = str(payload.get("host") or "").strip() or None

    if prefer_ip:
        host = ip or custom_domain or fallback or ""
    else:
        host = custom_domain or ip or fallback or ""
    return host, ip, custom_domain


def _extract_private_server(payload: dict[str, Any]) -> bool:
    settings = _extract_game_settings(payload)
    return bool(settings.get("private_server"))


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

    async def get_server(self, *, prefer_ip: bool = True) -> DatHostServerInfo:
        url = f"{self.base_url}/game-servers/{self.server_id}"
        async with aiohttp.ClientSession(timeout=self.timeout, auth=self._auth) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                payload: dict[str, Any] = await response.json()

        ports = payload.get("ports") or {}
        game_port = int(ports.get("game") or ports.get("game_port") or 27015)
        host, ip, custom_domain = _extract_connect_endpoints(payload, prefer_ip=prefer_ip)
        return DatHostServerInfo(
            server_id=self.server_id,
            host=host,
            game_port=game_port,
            name=str(payload.get("name") or "DatHost CS2 Server"),
            online=bool(payload.get("on") or payload.get("online")),
            booting=bool(payload.get("booting")),
            game_password=_extract_game_password(payload),
            ip=ip,
            custom_domain=custom_domain,
            private_server=_extract_private_server(payload),
        )

    async def wait_until_ready(
        self,
        *,
        timeout_seconds: int = 360,
        poll_interval: int = 10,
    ) -> DatHostServerInfo:
        """Wait until DatHost reports the server online and not booting."""
        attempts = max(1, timeout_seconds // poll_interval)
        last_info: DatHostServerInfo | None = None

        for attempt in range(attempts):
            info = await self.get_server()
            last_info = info
            if info.is_ready_for_players:
                if attempt > 0:
                    logger.info(
                        "DatHost server %s ready for players after ~%ss",
                        info.server_id,
                        attempt * poll_interval,
                    )
                return info

            state = "booting" if info.booting else "offline"
            logger.info(
                "DatHost server %s not ready yet (%s, on=%s) — waiting %ss",
                info.server_id,
                state,
                info.online,
                poll_interval,
            )
            await asyncio.sleep(poll_interval)

        if last_info is None:
            raise TimeoutError("DatHost server status could not be retrieved")
        raise TimeoutError(
            f"DatHost server {last_info.server_id} still not ready "
            f"(on={last_info.online}, booting={last_info.booting})"
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
