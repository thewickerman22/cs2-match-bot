from __future__ import annotations

import re
from urllib.parse import quote

STEAM64_PATTERN = re.compile(r"^\d{17}$")
STEAM_LEGACY_PATTERN = re.compile(r"^STEAM_[0-5]:([0-1]):(\d+)$", re.IGNORECASE)
STEAM64_BASE = 76561197960265728
CS2_APP_ID = 730


def normalize_steam_id(raw_value: str) -> str:
    value = raw_value.strip().strip("<>")
    if not value:
        raise ValueError("Steam ID cannot be empty.")

    if value.startswith("http://") or value.startswith("https://"):
        value = value.split("?")[0].rstrip("/")
        if "/profiles/" in value:
            value = value.rsplit("/profiles/", 1)[-1].split("/")[0]
        elif "/id/" in value:
            raise ValueError(
                "Custom Steam URLs (`/id/yourname`) are not supported. "
                "Open https://steamid.io, paste your profile link, and use the **steamID64** value."
            )
        else:
            raise ValueError(
                "Unrecognized Steam profile URL. Use a `/profiles/7656119...` link or the 17-digit steamID64."
            )

    legacy_match = STEAM_LEGACY_PATTERN.match(value)
    if legacy_match:
        y = int(legacy_match.group(1))
        z = int(legacy_match.group(2))
        value = str(STEAM64_BASE + z * 2 + y)

    if not STEAM64_PATTERN.fullmatch(value):
        raise ValueError(
            "Invalid Steam64 ID. Use the **17-digit steamID64** from https://steamid.io "
            "(not your display name or `/id/` profile URL)."
        )
    return value


def format_team(players: list, label: str) -> str:
    lines = [f"**{label}**"]
    for player in players:
        lines.append(f"- {player.discord_name} (`{player.steam_id}`)")
    return "\n".join(lines)


def sanitize_console_command_for_log(command: str) -> str:
    """Redact secrets from server console commands before logging."""
    if "matchzy_loadmatch_url" not in command:
        return command
    return re.sub(
        r'(matchzy_loadmatch_url "[^"]+" "[^"]+" ")[^"]+(")',
        r'\1***\2',
        command,
    )


def build_connect_info(host: str, port: int, password: str | None = None) -> str:
    connect = f"connect {host}:{port}"
    if password:
        connect += f"; password {password}"
    return f"`{connect}`"


def build_steam_connect_url(host: str, port: int, password: str | None = None) -> str:
    """Build a steam:// URL that launches CS2 and connects to the server."""
    command = f"+connect {host}:{port}"
    if password:
        command += f"; password {password}"
    return f"steam://run/{CS2_APP_ID}//{quote(command, safe='+:;')}"


def build_server_connect_field(host: str, port: int, password: str | None = None) -> str:
    steam_url = build_steam_connect_url(host, port, password)
    lines = [
        f"**[Join via Steam]({steam_url})**",
        build_connect_info(host, port, password),
    ]
    return "\n".join(lines)
