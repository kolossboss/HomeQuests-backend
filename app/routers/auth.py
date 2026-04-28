from __future__ import annotations

import logging
from contextlib import contextmanager
from threading import Lock

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal, engine, get_db
from ..db_tools import (
    DbToolsError,
    backup_allowed_dirs,
    backup_supported,
    list_backup_files,
    pg_restore_available,
    restore_backup,
    store_uploaded_backup,
)
from ..deps import get_current_user
from ..models import Family, FamilyMembership, RoleEnum, User
from ..schemas import (
    BootstrapBackupFileOut,
    BootstrapBackupListOut,
    BootstrapBackupUploadOut,
    BootstrapRequest,
    BootstrapRestoreOut,
    BootstrapRestoreRequest,
    BootstrapStatusOut,
    LoginRequest,
    TokenResponse,
    UserOut,
)
from ..security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])
COOKIE_NAME = "fp_token"
COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 30
BOOTSTRAP_LOCK_KEY = 930_000_001
_bootstrap_fallback_lock = Lock()
logger = logging.getLogger(__name__)


def _request_uses_https(request: Request) -> bool:
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return request.url.scheme == "https" or forwarded_proto == "https"


def _set_auth_cookie(response: Response, token: str, request: Request) -> None:
    # In HTTPS contexts immer secure setzen; per Setting kann dies global erzwungen werden.
    cookie_secure = bool(settings.auth_cookie_secure or _request_uses_https(request))
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=cookie_secure,
        path="/",
    )


def _mask_identifier(identifier: str) -> str:
    raw = (identifier or "").strip()
    if not raw:
        return "<leer>"
    if "@" in raw:
        local, _, domain = raw.partition("@")
        local_masked = (local[:2] + "***") if local else "***"
        return f"{local_masked}@{domain}"
    return (raw[:2] + "***") if len(raw) > 2 else "***"


@contextmanager
def _bootstrap_guard(db: Session):
    # Verhindert parallele Bootstrap-Initialisierungen (mehrere Worker/Requests).
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": BOOTSTRAP_LOCK_KEY})
        yield
        return

    _bootstrap_fallback_lock.acquire()
    try:
        yield
    finally:
        _bootstrap_fallback_lock.release()


@router.get("/bootstrap-status", response_model=BootstrapStatusOut)
def bootstrap_status(db: Session = Depends(get_db)):
    has_user = db.query(User.id).first() is not None
    return BootstrapStatusOut(bootstrap_required=not has_user)


@router.get("/bootstrap-backups", response_model=BootstrapBackupListOut)
def bootstrap_backups(db: Session = Depends(get_db)):
    has_user = db.query(User.id).first() is not None
    if has_user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bootstrap bereits erfolgt")

    files = list_backup_files(limit=200)
    return BootstrapBackupListOut(
        backup_supported=backup_supported(),
        restore_command_available=pg_restore_available(),
        backup_allowed_dirs=[str(entry) for entry in backup_allowed_dirs()],
        upload_max_bytes=int(settings.db_backup_upload_max_bytes),
        files=[
            BootstrapBackupFileOut(
                file_name=item.file_name,
                file_path=item.file_path,
                size_bytes=item.size_bytes,
                modified_at_utc=item.modified_at_utc,
            )
            for item in files
        ],
    )


@router.post("/bootstrap-backups/upload", response_model=BootstrapBackupUploadOut)
def bootstrap_backup_upload(
    file: UploadFile = File(...),
    target_dir: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    try:
        has_user = db.query(User.id).first() is not None
        if has_user:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bootstrap bereits erfolgt")

        if not file.filename:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Dateiname fehlt")

        saved = store_uploaded_backup(
            file_obj=file.file,
            original_filename=file.filename,
            target_dir=(target_dir or None),
            max_bytes=settings.db_backup_upload_max_bytes,
        )
    except DbToolsError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc) or "Upload fehlgeschlagen") from exc
    finally:
        file.file.close()

    return BootstrapBackupUploadOut(
        uploaded=True,
        file_name=saved.file_name,
        file_path=saved.file_path,
        size_bytes=saved.size_bytes,
        uploaded_at_utc=saved.modified_at_utc,
    )


def _bootstrap_restore_error_status(message: str) -> int:
    user_errors = (
        "Backup-Datei",
        "Backup-Ziel",
        "Nur .dump",
        "Ungültige DATABASE_URL",
        "Restore ist aktuell nur für PostgreSQL",
        "pg_restore ist im Backend-Container nicht verfügbar",
    )
    if any(token in message for token in user_errors):
        return status.HTTP_400_BAD_REQUEST
    return status.HTTP_500_INTERNAL_SERVER_ERROR


@router.post("/bootstrap-restore", response_model=BootstrapRestoreOut)
def bootstrap_restore(payload: BootstrapRestoreRequest, db: Session = Depends(get_db)):
    has_user = db.query(User.id).first() is not None
    if has_user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bootstrap bereits erfolgt")

    # Laufende DB-Sessions schließen, damit pg_restore konsistent schreiben kann.
    db.close()
    engine.dispose()
    try:
        result = restore_backup(backup_file=payload.backup_file)
    except DbToolsError as exc:
        detail = str(exc) or "Restore fehlgeschlagen"
        raise HTTPException(status_code=_bootstrap_restore_error_status(detail), detail=detail) from exc

    verify_db = SessionLocal()
    try:
        user_count = int(verify_db.query(func.count(User.id)).scalar() or 0)
    finally:
        verify_db.close()

    if user_count < 1:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Restore abgeschlossen, aber es wurden keine Nutzer gefunden.",
        )

    return BootstrapRestoreOut(
        restored=True,
        backup_file_path=result.backup_file_path,
        duration_seconds=result.duration_seconds,
        restored_at_utc=result.restored_at_utc,
        database_engine=result.database_engine,
        user_count=user_count,
    )


@router.post("/bootstrap", response_model=TokenResponse)
def bootstrap(payload: BootstrapRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    with _bootstrap_guard(db):
        existing = db.query(User.id).first() is not None
        if existing:
            logger.info("Bootstrap abgelehnt: bereits initialisiert")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bootstrap bereits erfolgt")

        email = payload.email.lower() if payload.email else None

        family = Family(name="Haushalt")
        user = User(
            email=email,
            display_name=payload.display_name,
            password_hash=hash_password(payload.password),
        )
        db.add(family)
        db.add(user)
        db.flush()

        membership = FamilyMembership(family_id=family.id, user_id=user.id, role=RoleEnum.admin)
        db.add(membership)
        db.commit()

    token = create_access_token(str(user.id))
    _set_auth_cookie(response, token, request)
    logger.info("Bootstrap erfolgreich abgeschlossen für Nutzer-ID %s", user.id)
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    identifier = (payload.login or (payload.email or "")).strip()
    if not identifier:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Login fehlt")

    user = db.query(User).filter(User.email == identifier.lower()).first()

    if not user:
        users_by_name = (
            db.query(User)
            .filter(func.lower(User.display_name) == identifier.lower())
            .all()
        )
        if len(users_by_name) > 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Anzeigename ist nicht eindeutig. Bitte mit E-Mail anmelden.",
            )
        user = users_by_name[0] if users_by_name else None

    if not user or not verify_password(payload.password, user.password_hash):
        logger.warning(
            "Login fehlgeschlagen (identifier=%s, ip=%s)",
            _mask_identifier(identifier),
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Falsche Zugangsdaten")

    token = create_access_token(str(user.id))
    _set_auth_cookie(response, token, request)
    logger.info("Login erfolgreich für Nutzer-ID %s", user.id)
    return TokenResponse(access_token=token)


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return {"logged_out": True}


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user
