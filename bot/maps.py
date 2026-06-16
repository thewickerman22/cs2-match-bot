from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Cs2Map:
    map_id: str
    name: str
    pool: str


def _maps(entries: list[tuple[str, str, str]]) -> list[Cs2Map]:
    return [Cs2Map(map_id=map_id, name=name, pool=pool) for map_id, name, pool in entries]


CS2_MAPS: list[Cs2Map] = _maps(
    [
        # Active Duty (Premier / pro rotation)
        ("de_ancient", "Ancient", "Active Duty"),
        ("de_anubis", "Anubis", "Active Duty"),
        ("de_dust2", "Dust II", "Active Duty"),
        ("de_inferno", "Inferno", "Active Duty"),
        ("de_mirage", "Mirage", "Active Duty"),
        ("de_nuke", "Nuke", "Active Duty"),
        ("de_overpass", "Overpass", "Active Duty"),
        # Reserve / competitive rotation
        ("de_train", "Train", "Reserve"),
        ("de_vertigo", "Vertigo", "Reserve"),
        ("de_stronghold", "Stronghold", "Reserve"),
        ("de_warden", "Warden", "Reserve"),
        ("de_golden", "Golden", "Reserve"),
        ("de_palacio", "Palacio", "Reserve"),
        ("de_cache", "Cache", "Reserve"),
        ("de_cbble", "Cobblestone", "Reserve"),
        ("de_grail", "Grail", "Reserve"),
        ("de_jura", "Jura", "Reserve"),
        ("de_basalt", "Basalt", "Reserve"),
        ("de_edin", "Edin", "Reserve"),
        # Hostage Rescue
        ("cs_agency", "Agency", "Hostage"),
        ("cs_alpine", "Alpine", "Hostage"),
        ("cs_italy", "Italy", "Hostage"),
        ("cs_office", "Office", "Hostage"),
        # Wingman (one-site defusal)
        ("de_sanctum", "Sanctum", "Wingman"),
        ("de_poseidon", "Poseidon", "Wingman"),
        ("de_rooftop", "Rooftop", "Wingman"),
        ("de_assembly", "Assembly", "Wingman"),
        ("de_brewery", "Brewery", "Wingman"),
        ("de_dogtown", "Dogtown", "Wingman"),
        ("de_memento", "Memento", "Wingman"),
        ("de_palais", "Palais", "Wingman"),
        ("de_whistle", "Whistle", "Wingman"),
        ("de_transit", "Transit", "Wingman"),
        # Arms Race
        ("ar_baggage", "Baggage", "Arms Race"),
        ("ar_shoots", "Shoots", "Arms Race"),
        ("ar_shoots_night", "Shoots (Night)", "Arms Race"),
        ("ar_pool_day", "Pool Day", "Arms Race"),
        # Night / alternate variants
        ("de_ancient_night", "Ancient (Night)", "Variant"),
        # Discontinued (formerly official in CS2)
        ("de_mills", "Mills", "Discontinued"),
        ("de_thera", "Thera", "Discontinued"),
        # Legacy (CS:GO / deathmatch / workshop favorites)
        ("de_canals", "Canals", "Legacy"),
        ("de_lake", "Lake", "Legacy"),
        ("de_dust", "Dust", "Legacy"),
        ("de_sugarcane", "Sugarcane", "Legacy"),
        ("de_tuscan", "Tuscan", "Legacy"),
    ]
)

# Common alternate map ids (old names, typos, pre-CS2 ids).
MAP_ALIASES: dict[str, str] = {
    "de_alpine": "cs_alpine",
    "de_cobblestone": "de_cbble",
}


def normalize_map_id(map_id: str) -> str:
    return MAP_ALIASES.get(map_id, map_id)


MAP_BY_ID: dict[str, Cs2Map] = {game_map.map_id: game_map for game_map in CS2_MAPS}
MAP_IDS: frozenset[str] = frozenset(MAP_BY_ID)

ACTIVE_DUTY_MAPS: list[Cs2Map] = [
    game_map for game_map in CS2_MAPS if game_map.pool == "Active Duty"
]
PREMIER_VETO_POOL: frozenset[str] = frozenset(
    game_map.map_id for game_map in ACTIVE_DUTY_MAPS
)


def is_valid_map(map_id: str) -> bool:
    return normalize_map_id(map_id) in MAP_IDS


def map_display_name(map_id: str) -> str:
    game_map = MAP_BY_ID.get(normalize_map_id(map_id))
    if game_map is None:
        return map_id
    return game_map.name


def validate_map_or_default(map_id: str | None, default_map: str) -> str:
    selected = normalize_map_id(map_id or default_map)
    if not is_valid_map(selected):
        known = ", ".join(f"`{game_map.map_id}`" for game_map in CS2_MAPS[:8])
        raise ValueError(
            f"Unknown map `{map_id or default_map}`. Use a valid CS2 map id such as {known}, etc."
        )
    return selected
