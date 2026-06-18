from __future__ import annotations

import asyncio
import logging
import socket

logger = logging.getLogger(__name__)

_HEADER = b"\xFF\xFF\xFF\xFF"
_A2S_INFO_REQUEST = b"\xFF\xFF\xFF\xFF\x54Source Engine Query\x00"
_A2S_INFO_RESPONSE = 0x49
_A2S_CHALLENGE_RESPONSE = 0x41


def _query_a2s_sync(host: str, port: int, timeout: float) -> bool:
    """Return True when the game port responds to a Source A2S_INFO query."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(_A2S_INFO_REQUEST, (host, port))
        data, _ = sock.recvfrom(4096)
        if len(data) < 5 or not data.startswith(_HEADER):
            return False

        message_type = data[4]
        if message_type == _A2S_INFO_RESPONSE:
            return True

        if message_type == _A2S_CHALLENGE_RESPONSE and len(data) >= 9:
            challenge = data[5:9]
            sock.sendto(_A2S_INFO_REQUEST + challenge, (host, port))
            data, _ = sock.recvfrom(4096)
            return len(data) >= 5 and data[4] == _A2S_INFO_RESPONSE

        return False
    except OSError:
        return False
    finally:
        sock.close()


async def query_a2s(host: str, port: int, *, timeout: float = 3.0) -> bool:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: _query_a2s_sync(host, port, timeout),
    )


async def wait_until_a2s_responsive(
    host: str,
    port: int,
    *,
    timeout_seconds: int = 90,
    poll_interval: int = 5,
) -> bool:
    """Poll until the game port accepts UDP queries or timeout."""
    attempts = max(1, timeout_seconds // max(poll_interval, 1))
    for attempt in range(attempts):
        if await query_a2s(host, port, timeout=min(3.0, float(poll_interval))):
            if attempt > 0:
                logger.info(
                    "Game port %s:%s accepting UDP queries after ~%ss",
                    host,
                    port,
                    attempt * poll_interval,
                )
            return True
        logger.info(
            "Game port %s:%s not responding yet — retry in %ss (%s/%s)",
            host,
            port,
            poll_interval,
            attempt + 1,
            attempts,
        )
        await asyncio.sleep(poll_interval)
    return False
