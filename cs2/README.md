# MatchZy config overrides (optional)
# These files are bind-mounted into the CS2 container if you customize cs2-data.
# The xbird/cs2-matchzy image ships with MatchZy pre-installed.

# Example webhook URL for MatchZy events (set in csgo/cfg/MatchZy/config.cfg):
# matchzy_remote_log_url "http://match-bot:8080/matchzy/events"
# matchzy_remote_log_header_key "X-API-Key"
# matchzy_remote_log_header_value "your-matchzy-api-key-from-env"

# Recommended settings for Discord-managed matches:
# matchzy_kick_when_no_match_loaded 0
# matchzy_whitelist_enabled_default 1
# matchzy_minimum_ready_required 0
