from __future__ import annotations

import json
import logging
import asyncio
from collections.abc import Awaitable, Callable
from typing import Union

from aiohttp import web

from config import Settings
from matchmaker import Matchmaker
from matchzy import build_matchzy_config
from storage import Storage

logger = logging.getLogger(__name__)


MatchEventHandler = Callable[[str, dict], Union[Awaitable[None], None]]


class MatchHttpServer:
    def __init__(
        self,
        settings: Settings,
        matchmaker: Matchmaker,
        storage: Storage,
        on_match_event: MatchEventHandler | None = None,
    ) -> None:
        self.settings = settings
        self.matchmaker = matchmaker
        self.storage = storage
        self.on_match_event = on_match_event
        self.app = web.Application()
        self.app.router.add_get("/health", self.health)
        self.app.router.add_get("/matches/{match_id}.json", self.get_match_config)
        self.app.router.add_post("/matchzy/events", self.post_match_event)

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def get_match_config(self, request: web.Request) -> web.Response:
        match_id = request.match_info["match_id"]
        if request.headers.get("X-API-Key") != self.settings.matchzy_api_key:
            raise web.HTTPUnauthorized(text="Invalid API key")

        match = self.matchmaker.get_match(match_id)
        if match is not None:
            payload = build_matchzy_config(match, self.settings)
        else:
            payload_json = await self.storage.get_match_payload_json(match_id)
            if payload_json is None:
                raise web.HTTPNotFound(text="Match not found")
            payload = json.loads(payload_json)

        return web.Response(
            body=json.dumps(payload, indent=2),
            content_type="application/json",
        )

    async def post_match_event(self, request: web.Request) -> web.Response:
        if request.headers.get("X-API-Key") != self.settings.matchzy_api_key:
            raise web.HTTPUnauthorized(text="Invalid API key")

        try:
            payload = await request.json()
        except json.JSONDecodeError:
            raise web.HTTPBadRequest(text="Invalid JSON payload")

        event_name = str(payload.get("event", "unknown"))
        logger.info("MatchZy event received: %s", event_name)

        if self.on_match_event is not None:
            result = self.on_match_event(event_name, payload)
            if asyncio.iscoroutine(result):
                await result

        return web.json_response({"received": True})

    async def start(self) -> web.AppRunner:
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.settings.http_host, self.settings.http_port)
        await site.start()
        logger.info(
            "HTTP server listening on http://%s:%s",
            self.settings.http_host,
            self.settings.http_port,
        )
        return runner
