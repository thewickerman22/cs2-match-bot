# DatHost CS2 Server Setup

Use this guide when `CS2_SERVER_PROVIDER=dathost` in your `.env` file.

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
```

DatHost sends MatchZy commands through the **DatHost Console API** (not standard RCON). The bot handles this automatically.

Optional player connect overrides (otherwise fetched from DatHost API):

```env
CS2_PUBLIC_HOST=123.45.67.89
CS2_PUBLIC_PORT=27015
CS2_PW=your_server_password
```

## 3. MatchZy webhook + match JSON (FTP)

Edit `csgo/cfg/MatchZy/config.cfg` on your DatHost server via FTP:

```cfg
matchzy_remote_log_url "https://your-bot.example.com/matchzy/events"
matchzy_remote_log_header_key "X-API-Key"
matchzy_remote_log_header_value "your-matchzy-api-key-from-env"
matchzy_kick_when_no_match_loaded 0
```

Reboot the server after saving.

## 4. Run the bot (bot only)

DatHost hosts the game server — run only the Discord bot:

```powershell
docker compose up -d --build match-bot
```

Or locally:

```powershell
cd bot
python main.py
```

## 5. Test the connection

In Discord, run:

```
/admin testserver
```

This verifies DatHost API credentials, fetches connect info, and sends a test console command.

## Notes

- `BOT_PUBLIC_URL` **must be publicly reachable over HTTPS** so MatchZy on DatHost can download match JSON.
- If the bot runs on your home PC, use a tunnel (Cloudflare Tunnel, ngrok, etc.) or host the bot on a VPS.
- DatHost also offers a separate **CS2 Match API**; this bot uses **MatchZy + Console API** instead.
