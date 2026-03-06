from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from threading import Lock

from sqlalchemy import text

from .config import settings
from .database import SessionLocal, engine
from .models import RecurrenceTypeEnum, Task, TaskStatusEnum
from .push_notifications import run_push_reminder_sweep_once
from .routers.tasks import _apply_penalties_for_family

logger = logging.getLogger(__name__)
PENALTY_LOCK_KEY = 860031
_fallback_penalty_lock = Lock()


def _acquire_penalty_lock(db) -> bool:
    if engine.dialect.name == "postgresql":
        return bool(db.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": PENALTY_LOCK_KEY}).scalar())
    return _fallback_penalty_lock.acquire(blocking=False)


def _release_penalty_lock(db) -> None:
    if engine.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": PENALTY_LOCK_KEY})
        return
    if _fallback_penalty_lock.locked():
        _fallback_penalty_lock.release()


def run_penalty_sweep_once() -> bool:
    with SessionLocal() as db:
        if not _acquire_penalty_lock(db):
            return False

        try:
            family_ids = [
                int(row[0])
                for row in (
                    db.query(Task.family_id)
                    .filter(
                        Task.is_active == True,  # noqa: E712
                        Task.recurrence_type.in_([RecurrenceTypeEnum.daily.value, RecurrenceTypeEnum.weekly.value]),
                        Task.penalty_enabled == True,  # noqa: E712
                        Task.penalty_points > 0,
                        Task.due_at.is_not(None),
                        Task.status.in_([TaskStatusEnum.open, TaskStatusEnum.rejected]),
                    )
                    .distinct()
                    .all()
                )
            ]

            changed = False
            for family_id in family_ids:
                changed = _apply_penalties_for_family(db, family_id) or changed

            if changed:
                db.commit()
            else:
                db.rollback()
            return changed
        except Exception:
            db.rollback()
            raise
        finally:
            with suppress(Exception):
                _release_penalty_lock(db)


async def penalty_worker() -> None:
    while True:
        try:
            await asyncio.to_thread(run_penalty_sweep_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Penalty-Worker fehlgeschlagen")
        await asyncio.sleep(settings.penalty_worker_interval_seconds)


async def push_worker() -> None:
    while True:
        try:
            await asyncio.to_thread(run_push_reminder_sweep_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Push-Worker fehlgeschlagen")
        await asyncio.sleep(settings.push_worker_interval_seconds)
