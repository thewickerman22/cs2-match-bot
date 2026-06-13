from __future__ import annotations

from dataclasses import dataclass

from discord import app_commands


@dataclass(frozen=True)
class Cs2Map:
    map_id: str
    name: str
    pool: str


def _maps(entries: list[tuple[str, str, str]]) -> list[Cs2Map]:
    return [Cs2Map(map_id=map_id, name=name, pool=pool) for map_id, name, pool in entries]


CS2_MAPS: list[Cs2Map] = _maps(
    [
        # Active Duty
        ("de_ancient", "Ancient", "Active Duty"),
        ("de_anubis", "Anubis", "Active Duty"),
        ("de_dust2", "Dust II", "Active Duty"),
        ("de_inferno", "Inferno", "Active Duty"),
        ("de_mirage", "Mirage", "Active Duty"),
        ("de_nuke", "Nuke", "Active Duty"),
        ("de_overpass", "Overpass", "Active Duty"),
        # Reserve / Competitive
        ("de_train", "Train", "Reserve"),
        ("de_vertigo", "Vertigo", "Reserve"),
        ("de_basalt", "Basalt", "Reserve"),
        ("de_edin", "Edin", "Reserve"),
        ("de_thera", "Thera", "Reserve"),
        ("de_mills", "Mills", "Reserve"),
        ("de_stronghold", "Stronghold", "Reserve"),
        ("de_warden", "Warden", "Reserve"),
        ("de_alpine", "Alpine", "Reserve"),
        # Hostage Rescue
        ("cs_office", "Office", "Hostage"),
        ("cs_italy", "Italy", "Hostage"),
        # Wingman
        ("de_sanctum", "Sanctum", "Wingman"),
        ("de_poseidon", "Poseidon", "Wingman"),
        # Arms Race
        ("ar_baggage", "Baggage", "Arms Race"),
        ("ar_shoots", "Shoots", "Arms Race"),
        ("ar_pool_day", "Pool Day", "Arms Race"),
        # Legacy / former competitive
        ("de_cache", "Cache", "Legacy"),
        ("de_cobblestone", "Cobblestone", "Legacy"),
        ("de_tuscan", "Tuscan", "Legacy"),
    ]
)

MAP_BY_ID: dict[str, Cs2Map] = {game_map.map_id: game_map for game_map in CS2_MAPS}
MAP_IDS: frozenset[str] = frozenset(MAP_BY_ID)

ACTIVE_DUTY_MAPS: list[Cs2Map] = [
    game_map for game_map in CS2_MAPS if game_map.pool == "Active Duty"
]
MAP_VOTE_1V1_POOL: frozenset[str] = frozenset(
    game_map.map_id for game_map in ACTIVE_DUTY_MAPS
)
PREMIER_VETO_POOL: frozenset[str] = MAP_VOTE_1V1_POOL


def is_valid_map(map_id: str) -> bool:
    return map_id in MAP_IDS


def map_display_name(map_id: str) -> str:
    game_map = MAP_BY_ID.get(map_id)
    if game_map is None:
        return map_id
    return game_map.name


def map_choices(limit: int = 25) -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=f"{game_map.name} ({game_map.pool})", value=game_map.map_id)
        for game_map in CS2_MAPS[:limit]
    ]


def map_autocomplete_choices(current: str, *, limit: int = 25) -> list[app_commands.Choice[str]]:
    query = current.lower().strip()
    matches: list[Cs2Map] = []
    for game_map in CS2_MAPS:
        if not query:
            matches.append(game_map)
        elif (
            query in game_map.map_id.lower()
            or query in game_map.name.lower()
            or query in game_map.pool.lower()
        ):
            matches.append(game_map)
        if len(matches) >= limit:
            break

    return [
        app_commands.Choice(
            name=f"{game_map.name} ({game_map.pool})",
            value=game_map.map_id,
        )
        for game_map in matches
    ]


def validate_map_or_default(map_id: str | None, default_map: str) -> str:
    selected = map_id or default_map
    if not is_valid_map(selected):
        known = ", ".join(f"`{game_map.map_id}`" for game_map in CS2_MAPS[:8])
        raise ValueError(
            f"Unknown map `{selected}`. Use a valid CS2 map id such as {known}, etc."
        )
    return selected
