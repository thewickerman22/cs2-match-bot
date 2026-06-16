# CS2 / MatchZy server notes

MatchZy config lives on the game server at `csgo/cfg/MatchZy/config.cfg`.

**DatHost:** use your public HTTPS bot URL for webhooks.  
**Local Docker:** use `http://match-bot:8080/matchzy/events` on the compose network.

```cfg
matchzy_remote_log_url "https://your-bot.example.com/matchzy/events"
matchzy_remote_log_header_key "X-API-Key"
matchzy_remote_log_header_value "your-matchzy-api-key-from-env"
matchzy_kick_when_no_match_loaded 0
matchzy_ffw_enabled 0
matchzy_gg_enabled 0
```

See [dathost-setup.md](dathost-setup.md) for full DatHost setup, Discord admin panel controls, and webhook verification.
