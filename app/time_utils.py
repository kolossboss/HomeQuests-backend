from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def app_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(settings.app_timezone)
    except ZoneInfoNotFoundError:
        logger.error("APP_TIMEZONE '%s' ist unbekannt; UTC wird verwendet", settings.app_timezone)
        return ZoneInfo("UTC")


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def app_local_now_naive() -> datetime:
    # Fachliche Fälligkeiten werden als lokale, naive Wall-Clock-Zeit gespeichert.
    return datetime.now(app_timezone()).replace(tzinfo=None)


def utc_timestamp_to_app_local_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return normalized.astimezone(app_timezone()).replace(tzinfo=None)


def app_local_to_utc_naive(value: datetime) -> datetime:
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=app_timezone())
    return normalized.astimezone(timezone.utc).replace(tzinfo=None)
