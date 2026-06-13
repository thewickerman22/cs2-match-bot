from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv

from bot_app import MatchBot
from config import ServerProvider, load_settings
from elo_service import EloService
from http_server import MatchHttpServer
from matchmaker import Matchmaker
from matchzy import MatchZyService
from server_connect import ServerConnectResolver
from server_console import create_server_console
from storage import Storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()
    settings = load_settings()

    storage = Storage(settings.database_path)
    await storage.initialize()

    next_match_id = await storage.initialize_match_id_counter()
    matchmaker = Matchmaker(default_map=settings.default_map, next_match_id=next_match_id)
    console = create_server_console(settings)
    matchzy = MatchZyService(settings, console)
    elo_service = EloService(storage, settings)
    connect_resolver = ServerConnectResolver(settings)
    bot = MatchBot(settings, storage, matchmaker, matchzy, elo_service, connect_resolver)

    if settings.server_provider == ServerProvider.DATHOST:
        logger.info("CS2 server provider: DatHost (%s)", settings.dathost_game_server_id)
    else:
        logger.info("CS2 server provider: local RCON (%s:%s)", settings.cs2_host, settings.cs2_port)

    async def on_match_event(event_name: str, payload: dict) -> None:
        await bot.handle_match_event(event_name, payload)

    http_server = MatchHttpServer(settings, matchmaker, storage, on_match_event=on_match_event)
    runner = await http_server.start()

    try:
        await bot.start(settings.discord_token)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down")
