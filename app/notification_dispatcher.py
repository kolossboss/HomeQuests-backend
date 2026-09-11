from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import json
import logging
import os
from threading import Event, Lock, Thread
from uuid import uuid4

from sqlalchemy import and_, or_

from .database import SessionLocal
from .models import LiveUpdateEvent, RemoteNotificationOutbox
from .push_notifications import dispatch_remote_pushes_for_event
from .time_utils import utc_now_naive

logger = logging.getLogger(__name__)

OUTBOX_PENDING = "pending"
OUTBOX_RETRY = "retry"
OUTBOX_PROCESSING = "processing"
OUTBOX_DEAD = "dead"
_MAX_ATTEMPTS = 8
_LEASE_SECONDS = 300
_POLL_INTERVAL_SECONDS = 1.0
_RETRY_BASE_SECONDS = 5
_RETRY_MAX_SECONDS = 15 * 60


@dataclass(frozen=True)
class RemoteDispatchJob:
    outbox_id: int
    family_id: int
    event_id: int
    event_type: str
    payload: dict | None
    worker_id: str


class RemoteDispatchAttemptFailed(RuntimeError):
    """A delivery attempt completed but at least one recipient failed."""


_stop_event = Event()
_wakeup_event = Event()
_worker_lock = Lock()
_sqlite_claim_lock = Lock()
_worker_thread: Thread | None = None
_worker_id = f"{os.getpid()}-{uuid4().hex}"


def start_remote_dispatcher() -> None:
    global _worker_thread
    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return
        _stop_event.clear()
        _wakeup_event.set()
        _worker_thread = Thread(target=_worker_loop, name="homequests-remote-dispatcher", daemon=True)
        _worker_thread.start()


def stop_remote_dispatcher(timeout_seconds: float = 5.0) -> None:
    global _worker_thread
    with _worker_lock:
        thread = _worker_thread
    if thread is None:
        return
    _stop_event.set()
    _wakeup_event.set()
    thread.join(timeout=timeout_seconds)
    with _worker_lock:
        if _worker_thread is thread and not thread.is_alive():
            _worker_thread = None


def enqueue_remote_dispatch_job(*, family_id: int, event_id: int, payload: dict | None) -> bool:
    """Wake the persistent worker; the outbox row was written before this hook.

    The arguments remain source-compatible with the former in-memory queue API.
    They are intentionally not used for delivery: polling the committed outbox
    is the correctness mechanism, while this signal is only a latency hint.
    """

    del family_id, event_id, payload
    thread = _worker_thread
    if thread is None or not thread.is_alive():
        return False
    _wakeup_event.set()
    return True


def _worker_loop() -> None:
    while not _stop_event.is_set():
        # Clear before polling. If a commit wakes us while polling, the event
        # remains set and the following wait returns immediately; no wakeup is
        # lost in the clear/wait transition.
        _wakeup_event.clear()
        while not _stop_event.is_set():
            try:
                job = _claim_next_outbox_job()
            except Exception:
                logger.exception("Remote-Dispatcher konnte Outbox nicht pollen")
                break
            if job is None:
                break
            _process_job(job)
        _wakeup_event.wait(_POLL_INTERVAL_SECONDS)


def _claim_next_outbox_job() -> RemoteDispatchJob | None:
    with SessionLocal() as db:
        dialect_name = db.get_bind().dialect.name
        claim_lock = _sqlite_claim_lock if dialect_name == "sqlite" else _NoopLock()
        with claim_lock:
            try:
                return _claim_next_outbox_job_in_session(db, dialect_name)
            except Exception:
                db.rollback()
                if dialect_name == "sqlite":
                    # SQLite has no SKIP LOCKED. The process-local lock plus the
                    # database write transaction is the intentionally small test/
                    # add-on fallback; PostgreSQL remains the multi-process path.
                    logger.debug("SQLite-Outbox konnte momentan nicht geclaimt werden", exc_info=True)
                    return None
                raise


class _NoopLock:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _claim_next_outbox_job_in_session(db, dialect_name: str) -> RemoteDispatchJob | None:
    now = utc_now_naive()
    stale_before = now - timedelta(seconds=_LEASE_SECONDS)
    eligible = or_(
        and_(
            RemoteNotificationOutbox.status.in_((OUTBOX_PENDING, OUTBOX_RETRY)),
            RemoteNotificationOutbox.available_at <= now,
        ),
        and_(
            RemoteNotificationOutbox.status == OUTBOX_PROCESSING,
            or_(
                RemoteNotificationOutbox.locked_at.is_(None),
                RemoteNotificationOutbox.locked_at <= stale_before,
            ),
        ),
    )
    while True:
        query = db.query(RemoteNotificationOutbox).filter(eligible).order_by(RemoteNotificationOutbox.id.asc())
        if dialect_name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        row = query.first()
        if row is None:
            db.commit()
            return None

        if row.attempt_count >= _MAX_ATTEMPTS:
            row.status = OUTBOX_DEAD
            row.locked_at = None
            row.locked_by = None
            row.last_error = f"Maximale Anzahl Zustellversuche erreicht ({_MAX_ATTEMPTS})"
            db.commit()
            continue

        row.status = OUTBOX_PROCESSING
        row.attempt_count += 1
        row.locked_at = now
        row.locked_by = _worker_id
        row.last_error = None
        db.flush()
        job = RemoteDispatchJob(
            outbox_id=int(row.id),
            family_id=int(row.family_id),
            event_id=int(row.event_id),
            event_type=row.event_type,
            payload=_parse_payload(row.payload_json),
            worker_id=_worker_id,
        )
        db.commit()
        return job


def _process_job(job: RemoteDispatchJob) -> None:
    try:
        with SessionLocal() as db:
            outbox = (
                db.query(RemoteNotificationOutbox)
                .filter(
                    RemoteNotificationOutbox.id == job.outbox_id,
                    RemoteNotificationOutbox.status == OUTBOX_PROCESSING,
                    RemoteNotificationOutbox.locked_by == job.worker_id,
                )
                .first()
            )
            if outbox is None:
                db.rollback()
                return

            event = (
                db.query(LiveUpdateEvent)
                .filter(
                    LiveUpdateEvent.id == job.event_id,
                    LiveUpdateEvent.family_id == job.family_id,
                )
                .first()
            )
            if event is None:
                # Live events are intentionally trimmed. The outbox snapshot
                # keeps delivery possible after that retention cleanup.
                event = LiveUpdateEvent(
                    id=job.event_id,
                    family_id=job.family_id,
                    event_type=job.event_type,
                    payload_json=json.dumps(job.payload, ensure_ascii=False) if job.payload is not None else None,
                )

            summary = dispatch_remote_pushes_for_event(
                db,
                family_id=job.family_id,
                event=event,
                payload=job.payload,
            )
            if summary.failed_count:
                # Keep successful delivery dedupe rows committed before the
                # retry state is written. This prevents avoidable re-sends of
                # recipients that succeeded in a partially failed attempt.
                db.commit()
                raise RemoteDispatchAttemptFailed(
                    f"{summary.failed_count} Remote-Zustellung(en) fehlgeschlagen"
                )

            # Delete and delivery-log writes are in one DB transaction. If that
            # commit fails after an external send, the row remains for retry;
            # a duplicate is possible in the crash window before the delivery
            # dedupe log commits, which is the unavoidable external-send limit.
            db.delete(outbox)
            db.commit()
    except Exception as exc:
        _mark_job_failed(job, exc)


def _mark_job_failed(job: RemoteDispatchJob, error: Exception) -> None:
    try:
        with SessionLocal() as db:
            query = (
                db.query(RemoteNotificationOutbox)
                .filter(
                    RemoteNotificationOutbox.id == job.outbox_id,
                    RemoteNotificationOutbox.status == OUTBOX_PROCESSING,
                    RemoteNotificationOutbox.locked_by == job.worker_id,
                )
            )
            if db.get_bind().dialect.name == "postgresql":
                query = query.with_for_update()
            row = query.first()
            if row is None:
                db.rollback()
                return

            now = utc_now_naive()
            row.last_error = _safe_error_text(error)
            row.locked_at = None
            row.locked_by = None
            if row.attempt_count >= _MAX_ATTEMPTS:
                row.status = OUTBOX_DEAD
                row.available_at = now
            else:
                row.status = OUTBOX_RETRY
                delay = min(
                    _RETRY_MAX_SECONDS,
                    _RETRY_BASE_SECONDS * (2 ** max(row.attempt_count - 1, 0)),
                )
                row.available_at = now + timedelta(seconds=delay)
            db.commit()
    except Exception:
        # A failed error update leaves the processing lease in place. Polling
        # will reclaim it after the lease timeout; the worker must stay alive.
        logger.exception(
            "Remote-Dispatcher konnte Outbox-Fehlerstatus nicht speichern",
            extra={"outbox_id": job.outbox_id},
        )


def _parse_payload(payload_json: str | None) -> dict | None:
    if not payload_json:
        return None
    try:
        payload = json.loads(payload_json)
    except (TypeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _safe_error_text(error: Exception) -> str:
    detail = " ".join(str(error).split())[:1000]
    return f"{type(error).__name__}: {detail}" if detail else type(error).__name__
