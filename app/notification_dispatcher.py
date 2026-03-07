from __future__ import annotations

from dataclasses import dataclass
import logging
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
import time

from .database import SessionLocal
from .models import LiveUpdateEvent
from .push_notifications import dispatch_remote_pushes_for_event

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RemoteDispatchJob:
    family_id: int
    event_id: int
    payload: dict | None


_QUEUE_MAX_SIZE = 5000
_RETRY_ATTEMPTS = 20
_queue: Queue[RemoteDispatchJob | None] = Queue(maxsize=_QUEUE_MAX_SIZE)
_stop_event = Event()
_worker_lock = Lock()
_worker_thread: Thread | None = None


def start_remote_dispatcher() -> None:
    global _worker_thread
    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return
        _stop_event.clear()
        _worker_thread = Thread(target=_worker_loop, name="homequests-remote-dispatcher", daemon=True)
        _worker_thread.start()


def stop_remote_dispatcher(timeout_seconds: float = 5.0) -> None:
    global _worker_thread
    with _worker_lock:
        thread = _worker_thread
        _worker_thread = None
    if thread is None:
        return
    _stop_event.set()
    try:
        _queue.put_nowait(None)
    except Full:
        pass
    thread.join(timeout=timeout_seconds)


def enqueue_remote_dispatch_job(*, family_id: int, event_id: int, payload: dict | None) -> bool:
    thread = _worker_thread
    if thread is None or not thread.is_alive():
        return False
    job = RemoteDispatchJob(family_id=family_id, event_id=event_id, payload=payload)
    try:
        _queue.put_nowait(job)
        return True
    except Full:
        logger.warning(
            "Remote-Dispatcher Queue voll; Event %s fuer Familie %s wird inline verarbeitet",
            event_id,
            family_id,
        )
        return False


def _worker_loop() -> None:
    while not _stop_event.is_set():
        try:
            job = _queue.get(timeout=0.5)
        except Empty:
            continue
        if job is None:
            _queue.task_done()
            break
        try:
            _process_job(job)
        except Exception:
            logger.exception(
                "Remote-Dispatcher Fehler bei Event %s (Familie %s)",
                job.event_id,
                job.family_id,
            )
        finally:
            _queue.task_done()


def _process_job(job: RemoteDispatchJob) -> None:
    for attempt in range(_RETRY_ATTEMPTS):
        with SessionLocal() as db:
            event = (
                db.query(LiveUpdateEvent)
                .filter(LiveUpdateEvent.id == job.event_id, LiveUpdateEvent.family_id == job.family_id)
                .first()
            )
            if event is not None:
                dispatch_remote_pushes_for_event(
                    db,
                    family_id=job.family_id,
                    event=event,
                    payload=job.payload,
                )
                db.commit()
                return
        time.sleep(0.15 * (attempt + 1))

    logger.info(
        "Remote-Dispatcher: Event %s in Familie %s nicht gefunden (vermutlich Rollback), Versand uebersprungen",
        job.event_id,
        job.family_id,
    )
