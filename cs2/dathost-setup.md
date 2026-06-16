# DatHost CS2 Server Setup

Use this guide when `CS2_SERVER_PROVIDER=dathost` in your `.env` file.

DatHost hosts the game server — you run **only the Discord bot** (on a VPS, home PC with tunnel, etc.). The bot talks to DatHost via the **Console API**, not standard RCON.

## 1. DatHost server requirements

On your [DatHost](https://dathost.net) CS2 server control panel:

1. Install **MatchZy** from **Mods & Plugins** (1-click installer).
2. Reboot the server after installing MatchZy.
3. Copy your **Game Server ID** from the control panel URL  
   (`https://dathost.net/control/game-servers/<THIS_ID>`).

## 2. Bot environment variables

```env
CS2_SERVER_PROVIDER=dathost
DATHOST_EMAIL=you@example.com
DATHOST_PASSWORD=your_dathost_password
DATHOST_GAME_SERVER_ID=6503b7832e1d23f8c5c6f762

# Must be a public HTTPS URL the DatHost server can reach
BOT_PUBLIC_URL=https://your-bot.example.com
MATCHZY_API_KEY=long-random-secret

# RCON status polling is local-only — disable on DatHost
MATCH_STATUS_POLL_SECONDS=0
```

DatHost sends MatchZy commands through the **DatHost Console API**. The bot handles this automatically.

Optional player connect overrides (otherwise fetched from DatHost API):

```env
CS2_PUBLIC_HOST=123.45.67.89
CS2_PUBLIC_PORT=27015
CS2_PW=your_server_password
```

Also set `DISCORD_GUILD_ID` so the bot auto-creates Discord channels on startup.

## 3. MatchZy webhook + match JSON (FTP)

Edit `csgo/cfg/MatchZy/config.cfg` on your DatHost server via FTP:

```cfg
matchzy_remote_log_url "https://your-bot.example.com/matchzy/events"
matchzy_remote_log_header_key "X-API-Key"
matchzy_remote_log_header_value "your-matchzy-api-key-from-env"
matchzy_kick_when_no_match_loaded 0
matchzy_ffw_enabled 0
matchzy_gg_enabled 0
```

- **`matchzy_remote_log_url`** — must match `BOT_PUBLIC_URL` + `/matchzy/events`.
- **`matchzy_remote_log_header_value`** — must match `MATCHZY_API_KEY` in `.env`.
- **`matchzy_ffw_enabled 0` / `matchzy_gg_enabled 0`** — prevent forfeit when a player disconnects mid-match.

Reboot the CS2 server after saving.

### Verify webhooks reach the bot

After a match, bot logs should show:

```text
POST /matchzy/events
MatchZy event received: map_result (match 3)
MatchZy event received: series_end (match 3)
Finishing match 3 from webhook series_end ...
Cleaned up team voice channels for match 3
```

If you only see `GET /matches/N.json` (match JSON load) but **never** `POST /matchzy/events`, the bot cannot auto-finish matches — fix `matchzy_remote_log_url` above and reboot the CS2 server.

Test manually (replace URL and API key):

```bash
curl -s https://your-bot.example.com/health

curl -X POST "https://your-bot.example.com/matchzy/events" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-matchzy-api-key-from-env" \
  -d '{"event":"series_end","matchid":"1","winner":{"team":"team1"},"team1_series_score":1,"team2_series_score":0,"time_until_restore":10}'
```

Expected response: `{"received": true}`.

### If webhooks still fail

Until webhooks work, roster players can use **Report: Team Alpha/Bravo Won** on the live embed in `#match-results` (majority of match players must agree).

Admins can react **🛑** on the admin panel in `#bot-commands` to end the match, clean up voice channels, and reset the server (no ELO change).

## 4. Run the bot (bot only)

```bash
docker compose up -d --build match-bot
docker compose logs -f match-bot
```

Or locally:

```bash
cd bot
pip install -r requirements.txt
python main.py
```

On first deploy, see [Deploy on AWS](../README.md#deploy-on-aws-or-any-vps) in the main README for VPS setup, HTTPS reverse proxy, and database backup.

## 5. Discord setup and test

1. Start the bot with `DISCORD_GUILD_ID` set — channels are created automatically, or click **Refresh Setup** on the admin panel in `#bot-commands`.
2. React **🔌** on the pinned admin panel in `#bot-commands` to test DatHost credentials, fetch connect info, and send a test console command.
3. Link Steam via **Link Steam Account** on `#bot-commands` before queueing.

### Admin panel quick reference (`#bot-commands`)

| Control | Action |
|---|---|
| **Refresh Setup** (button) | Create or refresh all matchmaking channels and panels |
| 🛑 | End active match (no ELO, cleanup voice) |
| ▶️ | Force-start MatchZy |
| 🔌 | Test DatHost / server connection |
| 1️⃣ / 2️⃣ / 5️⃣ | Reset captain lobby for 1v1 / 2v2 / 5v5 |

Admin reactions require bot admin role (`DISCORD_ADMIN_ROLE_ID`) or server administrator. **Refresh Setup** also allows **Manage Channels**.

## Notes

- `BOT_PUBLIC_URL` **must be publicly reachable over HTTPS** so MatchZy on DatHost can download match JSON and POST webhooks.
- If the bot runs on your home PC, use a tunnel (Cloudflare Tunnel, ngrok, etc.) or host the bot on a VPS.
- DatHost also offers a separate **CS2 Match API**; this bot uses **MatchZy + Console API** instead.
- Match finish fallback: if `series_end` never arrives, the bot finishes from `map_result` after `MAP_RESULT_FINISH_FALLBACK_SECONDS` (default 20).
