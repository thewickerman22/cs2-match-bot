from __future__ import annotations

import html
import json
import re
from typing import TYPE_CHECKING
from urllib.parse import quote, urlencode

if TYPE_CHECKING:
    from matchzy import ActiveMatch, QueuedPlayer

STEAM64_PATTERN = re.compile(r"^\d{17}$")
STEAM_LEGACY_PATTERN = re.compile(r"^STEAM_[0-5]:([0-1]):(\d+)$", re.IGNORECASE)
STEAM64_BASE = 76561197960265728
CS2_APP_ID = 730
_INVALID_CONNECT_HOSTS = frozenset({"", "cs2-server", "127.0.0.1", "localhost"})


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
    return re.sub(r"(\?key=)[^\"&]+", r"\1***", command)


def build_connect_command(host: str, port: int, password: str | None = None) -> str:
    command = f"connect {host}:{port}"
    if password:
        command += f"; password {password}"
    return command


def build_connect_info(host: str, port: int, password: str | None = None) -> str:
    return f"`{build_connect_command(host, port, password)}`"


def is_valid_connect_host(host: str) -> bool:
    return host.strip().lower() not in _INVALID_CONNECT_HOSTS


def build_steam_connect_url(host: str, port: int, password: str | None = None) -> str:
    """Build a steam:// URL that launches CS2 (app 730) and connects to the server."""
    command = f"+connect {host}:{port}"
    if password:
        command += f"; password {password}"
    return f"steam://run/{CS2_APP_ID}//{quote(command, safe='')}"


def build_steam_connect_legacy_url(host: str, port: int, password: str | None = None) -> str:
    """Legacy steam://connect format — used as a fallback on the join page."""
    if password:
        return f"steam://connect/{host}:{port}/{quote(password, safe='')}"
    return f"steam://connect/{host}:{port}"


def build_steam_run_url(host: str, port: int, password: str | None = None) -> str:
    """Alias for the primary CS2 launch URL."""
    return build_steam_connect_url(host, port, password)


def build_join_page_url(
    public_url: str,
    host: str,
    port: int,
    password: str | None = None,
) -> str:
    """HTTPS link for Discord — opens a join page that redirects to steam://."""
    params: dict[str, str] = {"host": host, "port": str(port)}
    if password:
        params["password"] = password
    return f"{public_url.rstrip('/')}/join?{urlencode(params)}"


def build_join_redirect_html(host: str, port: int, password: str | None = None) -> str:
    """HTML page that redirects the browser to steam:// and shows a manual fallback."""
    steam_url = build_steam_connect_url(host, port, password)
    steam_legacy_url = build_steam_connect_legacy_url(host, port, password)
    console_command = build_connect_command(host, port, password)
    steam_href = html.escape(steam_url, quote=True)
    steam_legacy_href = html.escape(steam_legacy_url, quote=True)
    console_text = html.escape(console_command)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Join CS2 Server</title>
  <meta http-equiv="refresh" content="0;url={steam_href}">
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 32rem; margin: 2rem auto; padding: 0 1rem; }}
    a {{ color: #66c0f4; }}
    code {{ background: #1b2838; color: #c7d5e0; padding: 0.2rem 0.4rem; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>Launching Counter-Strike 2…</h1>
  <p>Steam should open and connect you to <strong>{html.escape(host)}:{port}</strong>.</p>
  <p>If nothing happens:</p>
  <ul>
    <li><a href="{steam_href}">Launch CS2 and connect</a></li>
    <li><a href="{steam_legacy_href}">Connect with Steam</a> (alternate link)</li>
  </ul>
  <p>Or open the in-game console (<code>~</code>) and paste:</p>
  <p><code>{console_text}</code></p>
  <script>window.location.replace({json.dumps(steam_url)});</script>
</body>
</html>"""


def resolve_join_url(
    host: str,
    port: int,
    password: str | None,
    *,
    public_url: str | None,
) -> str:
    if public_url:
        return build_join_page_url(public_url, host, port, password)
    return build_steam_connect_url(host, port, password)


def _on_team1(match: ActiveMatch, discord_id: int) -> bool:
    return any(player.discord_id == discord_id for player in match.team1)


def _on_team2(match: ActiveMatch, discord_id: int) -> bool:
    return any(player.discord_id == discord_id for player in match.team2)


def _team_side_label(match: ActiveMatch, *, on_team1: bool) -> str:
    team1_is_ct = match.team1_side == "ct"
    if on_team1:
        return "CT" if team1_is_ct else "T"
    return "T" if team1_is_ct else "CT"


def player_assignment_label(match: ActiveMatch, discord_id: int) -> str | None:
    """Human-readable assignment, e.g. ``Team Alpha (CT)``."""
    if _on_team1(match, discord_id):
        team_name = "Team Alpha"
        side = _team_side_label(match, on_team1=True)
    elif _on_team2(match, discord_id):
        team_name = "Team Bravo"
        side = _team_side_label(match, on_team1=False)
    else:
        return None
    return f"{team_name} (**{side}**)"


def format_match_team_roster(
    match: ActiveMatch,
    players: list[QueuedPlayer],
    team_label: str,
    *,
    on_team1: bool,
    join_url: str | None = None,
) -> str:
    side = _team_side_label(match, on_team1=on_team1)
    lines = [f"## {side} · {team_label}"]
    if join_url:
        lines.append(f"# [Join — {side} side]({join_url})")
    for player in players:
        lines.append(f"• <@{player.discord_id}> · `{player.steam_id}` → **{side}**")
    return "\n".join(lines)


def build_match_connect_blocks(match: ActiveMatch, join_url: str) -> list[str]:
    """Prominent per-team join links for queue-status / match embeds."""
    return [
        f"# [Launch CS2 and connect]({join_url})",
        "_MatchZy assigns **CT** or **T** from your linked Steam ID when you connect._",
        format_match_team_roster(
            match,
            match.team1,
            "Team Alpha",
            on_team1=True,
            join_url=join_url,
        ),
        format_match_team_roster(
            match,
            match.team2,
            "Team Bravo",
            on_team1=False,
            join_url=join_url,
        ),
    ]


def build_server_connect_field(
    host: str,
    port: int,
    password: str | None = None,
    *,
    public_url: str | None = None,
    alternate_host: str | None = None,
    match: ActiveMatch | None = None,
) -> str:
    if not is_valid_connect_host(host):
        return (
            "⚠️ **Server address not loaded.** Ask an admin to react 🔌 on "
            "**#bot-commands**, or set `CS2_PUBLIC_HOST` and `CS2_PUBLIC_PORT` in the bot `.env`."
        )

    join_url = resolve_join_url(host, port, password, public_url=public_url)
    lines: list[str] = []

    if match is not None:
        lines.extend(build_match_connect_blocks(match, join_url))
    else:
        lines.extend(
            [
                f"# [Launch CS2 and connect]({join_url})",
                "_Opens CS2 and connects to the server._",
            ]
        )

    lines.append(
        f"Console fallback: `{build_connect_command(host, port, password)}`"
    )
    if alternate_host:
        alt_url = resolve_join_url(alternate_host, port, password, public_url=public_url)
        lines.append(f"# [Connect via IP]({alt_url})")
        lines.append(
            f"IP console: `{build_connect_command(alternate_host, port, password)}`"
        )
    lines.append(
        "_Tip: quit any offline/local game first. Timeout = server still loading or wrong port._"
    )
    return "\n".join(lines)
