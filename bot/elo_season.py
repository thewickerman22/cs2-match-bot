from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, datetime


SEASON_MONTHS = 3
SEASON_META_KEY = "elo_season_start"


@dataclass(frozen=True)
class EloSeason:
    start: datetime
    end: datetime

    @property
    def label(self) -> str:
        return f"{self.start.strftime('%b %d, %Y')} – {self.end.strftime('%b %d, %Y')}"


def utc_now() -> datetime:
    return datetime.now(UTC)


def parse_season_start(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day, tzinfo=value.tzinfo)


def build_season(start: datetime) -> EloSeason:
    normalized = start.astimezone(UTC)
    return EloSeason(start=normalized, end=add_months(normalized, SEASON_MONTHS))


def season_has_expired(season: EloSeason, now: datetime | None = None) -> bool:
    current = now or utc_now()
    return current >= season.end


def format_season_start(now: datetime | None = None) -> str:
    return (now or utc_now()).isoformat()
