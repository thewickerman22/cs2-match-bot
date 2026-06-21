from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Union

from aiohttp import web

from config import Settings
from matchmaker import Matchmaker
from matchzy import build_matchzy_config
from matchzy_events import (
    WebhookPayloadError,
    parse_event_payload,
    parse_webhook_json,
)
from storage import Storage
from utils import build_join_redirect_html

logger = logging.getLogger(__name__)

_JOIN_HOST_PATTERN = re.compile(r"^[a-zA-Z0-9.\-]+$")


MatchEventHandler = Callable[[dict], Union[Awaitable[None], None]]


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
        self.app.router.add_get("/", self.root)
        self.app.router.add_get("/health", self.health)
        self.app.router.add_get("/join", self.join_server)
        self.app.router.add_get("/matches/{match_id}.json", self.get_match_config)
        self.app.router.add_post("/matchzy/events", self.post_match_event)

    async def root(self, request: web.Request) -> web.Response:
        return web.json_response({"service": "cs2-match-bot", "status": "ok"})

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def join_server(self, request: web.Request) -> web.Response:
        if not self.matchmaker.active_matches:
            active_ids = await self.storage.get_active_match_ids()
            if not active_ids:
                raise web.HTTPForbidden(
                    text="No active match — connect links are disabled until a match is loaded."
                )

        host = request.query.get("host", "").strip()
        port_raw = request.query.get("port", "").strip()
        password = request.query.get("password", "").strip() or None

        if not host or not _JOIN_HOST_PATTERN.fullmatch(host):
            raise web.HTTPBadRequest(text="Invalid or missing host")
        try:
            port = int(port_raw)
        except ValueError as exc:
            raise web.HTTPBadRequest(text="Invalid or missing port") from exc
        if not 1 <= port <= 65535:
            raise web.HTTPBadRequest(text="Port out of range")

        body = build_join_redirect_html(host, port, password)
        return web.Response(text=body, content_type="text/html")

    async def get_match_config(self, request: web.Request) -> web.Response:
        match_id = request.match_info["match_id"]
        api_key = (
            request.headers.get("X-API-Key")
            or request.query.get("key")
            or ""
        )
        if api_key != self.settings.matchzy_api_key:
            logger.warning(
                "Unauthorized match JSON request for %s from %s",
                match_id,
                request.remote,
            )
            raise web.HTTPUnauthorized(text="Invalid API key")

        match = self.matchmaker.get_match(match_id)
        if match is not None:
            payload = build_matchzy_config(match, self.settings)
        else:
            payload_json = await self.storage.get_match_payload_json(match_id)
            if payload_json is None:
                raise web.HTTPNotFound(text="Match not found")
            payload = json.loads(payload_json)

        logger.info(
            "Serving match JSON for %s (map=%s) to %s",
            match_id,
            payload.get("maplist", ["?"])[0] if payload.get("maplist") else "?",
            request.remote,
        )
        return web.Response(
            body=json.dumps(payload, indent=2),
            content_type="application/json",
        )

    async def post_match_event(self, request: web.Request) -> web.Response:
        if request.headers.get("X-API-Key") != self.settings.matchzy_api_key:
            raise web.HTTPUnauthorized(text="Invalid API key")

        try:
            raw_body = await request.read()
            payload = parse_webhook_json(raw_body)
        except WebhookPayloadError as exc:
            logger.warning("MatchZy webhook rejected: %s", exc)
            raise web.HTTPBadRequest(text=str(exc)) from exc

        event_name, match_id = parse_event_payload(payload)
        logger.info("MatchZy event received: %s (match %s)", event_name, match_id)

        if self.on_match_event is not None:
            try:
                result = self.on_match_event(payload)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception(
                    "Failed to process MatchZy event %s (match %s)",
                    event_name,
                    match_id,
                )
                raise web.HTTPInternalServerError(text="Event processing failed")

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
