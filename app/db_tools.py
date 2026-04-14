from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from .config import settings
from .database import engine

SAFE_BACKUP_PREFIX_RE = re.compile(r"[^A-Za-z0-9._-]+")
SAFE_BACKUP_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class DbToolsError(RuntimeError):
    pass


@dataclass
class DbBackupFileInfo:
    file_name: str
    file_path: str
    size_bytes: int
    modified_at_utc: datetime


@dataclass
class DbBackupResult:
    file_path: str
    file_size_bytes: int
    duration_seconds: float
    created_at_utc: datetime
    database_engine: str


@dataclass
class DbRestoreResult:
    backup_file_path: str
    duration_seconds: float
    restored_at_utc: datetime
    database_engine: str


def database_engine_name() -> str:
    return str(engine.dialect.name or "unknown")


def backup_supported() -> bool:
    return database_engine_name() == "postgresql"


def pg_dump_available() -> bool:
    return shutil.which("pg_dump") is not None


def pg_restore_available() -> bool:
    return shutil.which("pg_restore") is not None


def psql_available() -> bool:
    return shutil.which("psql") is not None


def backup_allowed_dirs() -> list[Path]:
    dirs: list[Path] = []
    for raw in settings.db_backup_allowed_dirs:
        normalized = Path(raw).expanduser().resolve(strict=False)
        if normalized not in dirs:
            dirs.append(normalized)
    return dirs


def backup_default_dir() -> Path:
    allowed = backup_allowed_dirs()
    if not allowed:
        raise DbToolsError("Keine Backup-Zielpfade konfiguriert")
    if settings.db_backup_default_dir:
        return Path(settings.db_backup_default_dir).expanduser().resolve(strict=False)
    return allowed[0]


def sanitize_backup_prefix(prefix: str) -> str:
    cleaned = SAFE_BACKUP_PREFIX_RE.sub("_", (prefix or "").strip())
    cleaned = cleaned.strip("._-")
    return cleaned or "homequests"


def sanitize_backup_filename(filename: str | None) -> str:
    candidate = Path(str(filename or "").strip()).name
    stem = Path(candidate).stem
    stem_clean = SAFE_BACKUP_FILENAME_RE.sub("_", stem).strip("._-")
    return stem_clean or "homequests_upload"


def resolve_backup_target_dir(requested_dir: str | None) -> Path:
    allowed = backup_allowed_dirs()
    default_dir = backup_default_dir()
    target = default_dir if not requested_dir else Path(requested_dir).expanduser().resolve(strict=False)
    if not target.is_absolute():
        raise DbToolsError("Backup-Ziel muss ein absoluter Pfad sein")

    if not any(target.is_relative_to(base) for base in allowed):
        raise DbToolsError(
            "Backup-Ziel ist nicht erlaubt. "
            f"Erlaubte Basispfade: {', '.join(str(entry) for entry in allowed)}"
        )

    target.mkdir(parents=True, exist_ok=True)
    return target


def list_backup_files(*, limit: int = 200) -> list[DbBackupFileInfo]:
    files: list[DbBackupFileInfo] = []
    for base in backup_allowed_dirs():
        if not base.exists() or not base.is_dir():
            continue
        for entry in base.iterdir():
            if not entry.is_file():
                continue
            if entry.suffix.lower() != ".dump":
                continue
            stat = entry.stat()
            files.append(
                DbBackupFileInfo(
                    file_name=entry.name,
                    file_path=str(entry),
                    size_bytes=int(stat.st_size),
                    modified_at_utc=datetime.utcfromtimestamp(stat.st_mtime),
                )
            )
    files.sort(key=lambda item: item.modified_at_utc, reverse=True)
    return files[: max(1, min(int(limit), 500))]


def resolve_backup_file_path(path_or_name: str) -> Path:
    raw = (path_or_name or "").strip()
    if not raw:
        raise DbToolsError("Backup-Datei fehlt")

    candidate = Path(raw).expanduser().resolve(strict=False)
    if candidate.is_absolute():
        if not any(candidate.is_relative_to(base) for base in backup_allowed_dirs()):
            raise DbToolsError("Backup-Datei liegt außerhalb der erlaubten Pfade")
        if not candidate.exists() or not candidate.is_file():
            raise DbToolsError("Backup-Datei nicht gefunden")
        if candidate.suffix.lower() != ".dump":
            raise DbToolsError("Nur .dump Backups werden unterstützt")
        return candidate

    for base in backup_allowed_dirs():
        probe = (base / raw).resolve(strict=False)
        if not any(probe.is_relative_to(allowed) for allowed in backup_allowed_dirs()):
            continue
        if probe.exists() and probe.is_file() and probe.suffix.lower() == ".dump":
            return probe

    raise DbToolsError("Backup-Datei nicht gefunden")


def resolve_backup_directory_path(path_or_none: str | None) -> Path:
    raw = str(path_or_none or "").strip()
    target = backup_default_dir() if not raw else Path(raw).expanduser().resolve(strict=False)
    if not target.is_absolute():
        raise DbToolsError("Verzeichnis muss ein absoluter Pfad sein")
    if not any(target.is_relative_to(base) for base in backup_allowed_dirs()):
        raise DbToolsError(
            "Verzeichnis liegt außerhalb der erlaubten Pfade. "
            f"Erlaubte Basispfade: {', '.join(str(entry) for entry in backup_allowed_dirs())}"
        )
    if not target.exists():
        target.mkdir(parents=True, exist_ok=True)
    if not target.is_dir():
        raise DbToolsError("Verzeichnis nicht gefunden")
    return target


def list_backup_directories(path_or_none: str | None = None) -> tuple[Path, Path | None, list[Path]]:
    current = resolve_backup_directory_path(path_or_none)
    entries = sorted(
        [entry for entry in current.iterdir() if entry.is_dir()],
        key=lambda entry: entry.name.lower(),
    )

    parent: Path | None = None
    for base in backup_allowed_dirs():
        if current == base:
            parent = None
            break
        if current.is_relative_to(base):
            candidate = current.parent
            if candidate == current:
                parent = None
            elif candidate.is_relative_to(base):
                parent = candidate
            break
    return current, parent, entries


def create_backup_directory(*, parent_dir: str, directory_name: str) -> Path:
    parent = resolve_backup_directory_path(parent_dir)
    name = str(directory_name or "").strip()
    if not name:
        raise DbToolsError("Ordnername fehlt")
    if name in {".", ".."}:
        raise DbToolsError("Ungültiger Ordnername")
    if "/" in name or "\\" in name:
        raise DbToolsError("Ordnername darf keine Pfadtrenner enthalten")

    target = (parent / name).resolve(strict=False)
    if not any(target.is_relative_to(base) for base in backup_allowed_dirs()):
        raise DbToolsError("Zielordner liegt außerhalb der erlaubten Pfade")
    if target.exists():
        raise DbToolsError("Ordner existiert bereits")

    target.mkdir(parents=False, exist_ok=False)
    return target


def store_uploaded_backup(
    *,
    file_obj,
    original_filename: str | None,
    target_dir: str | None = None,
    max_bytes: int | None = None,
) -> DbBackupFileInfo:
    target = resolve_backup_target_dir(target_dir)
    original = Path(str(original_filename or "").strip()).name
    if not original.lower().endswith(".dump"):
        raise DbToolsError("Nur .dump Backups werden unterstützt")

    max_size = int(max_bytes or settings.db_backup_upload_max_bytes)
    stem = sanitize_backup_filename(original)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    file_name = f"{stem}_{timestamp}.dump"
    target_path = target / file_name
    counter = 1
    while target_path.exists():
        target_path = target / f"{stem}_{timestamp}_{counter}.dump"
        counter += 1

    temp_path = target_path.with_suffix(".dump.uploading")
    chunk_size = 1024 * 1024
    written = 0
    try:
        with temp_path.open("wb") as handle:
            while True:
                chunk = file_obj.read(chunk_size)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_size:
                    raise DbToolsError(
                        f"Backup-Upload überschreitet Limit von {max_size} Bytes"
                    )
                handle.write(chunk)
        temp_path.replace(target_path)
        target_path.chmod(0o600)
        stat = target_path.stat()
        return DbBackupFileInfo(
            file_name=target_path.name,
            file_path=str(target_path),
            size_bytes=int(stat.st_size),
            modified_at_utc=datetime.utcfromtimestamp(stat.st_mtime),
        )
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def _pg_connection_parts() -> tuple[str, int, str, str | None, str]:
    db_url = make_url(settings.database_url)
    database_name = db_url.database
    host = db_url.host or "db"
    port = int(db_url.port or 5432)
    username = db_url.username
    password = db_url.password
    if not database_name:
        raise DbToolsError("Ungültige DATABASE_URL (DB-Name fehlt)")
    if not username:
        raise DbToolsError("Ungültige DATABASE_URL (Benutzer fehlt)")
    return host, port, username, password, database_name


def create_backup(*, target_dir: str | None, filename_prefix: str, timeout_seconds: int | None = None) -> DbBackupResult:
    if not backup_supported():
        raise DbToolsError("Backup ist aktuell nur für PostgreSQL implementiert")
    if not pg_dump_available():
        raise DbToolsError("pg_dump ist im Backend-Container nicht verfügbar")

    host, port, username, password, database_name = _pg_connection_parts()
    target = resolve_backup_target_dir(target_dir)
    prefix = sanitize_backup_prefix(filename_prefix)
    started = datetime.utcnow()
    timestamp = started.strftime("%Y%m%d_%H%M%S")
    file_path = target / f"{prefix}_{timestamp}.dump"

    cmd = [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--host",
        host,
        "--port",
        str(port),
        "--username",
        username,
        "--file",
        str(file_path),
        database_name,
    ]
    env = os.environ.copy()
    if password:
        env["PGPASSWORD"] = password
    timeout_value = int(timeout_seconds or settings.db_backup_timeout_seconds)
    started_perf = time.perf_counter()
    try:
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_value,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DbToolsError(f"Backup-Timeout nach {timeout_value} Sekunden") from exc
    finally:
        env.pop("PGPASSWORD", None)

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        safe_stderr = stderr[-400:] if stderr else "unbekannter Fehler"
        raise DbToolsError(f"Backup fehlgeschlagen: {safe_stderr}")

    if not file_path.exists():
        raise DbToolsError("Backup wurde nicht erstellt")

    duration = round(time.perf_counter() - started_perf, 3)
    file_size = int(file_path.stat().st_size)
    file_path.chmod(0o600)
    return DbBackupResult(
        file_path=str(file_path),
        file_size_bytes=file_size,
        duration_seconds=duration,
        created_at_utc=started,
        database_engine=database_engine_name(),
    )


def restore_backup(*, backup_file: str, timeout_seconds: int | None = None) -> DbRestoreResult:
    if not backup_supported():
        raise DbToolsError("Restore ist aktuell nur für PostgreSQL implementiert")
    if not pg_restore_available():
        raise DbToolsError("pg_restore ist im Backend-Container nicht verfügbar")
    if not psql_available():
        raise DbToolsError("psql ist im Backend-Container nicht verfügbar")

    backup_path = resolve_backup_file_path(backup_file)
    host, port, username, password, database_name = _pg_connection_parts()
    env = os.environ.copy()
    if password:
        env["PGPASSWORD"] = password
    timeout_value = int(timeout_seconds or settings.db_backup_timeout_seconds)
    started = datetime.utcnow()
    started_perf = time.perf_counter()
    try:
        # pg_restore kann Dumps neuerer Versionen enthalten (z. B. SET transaction_timeout),
        # die auf älteren Postgres-Servern fehlschlagen. Daher: erst SQL erzeugen, dann
        # bekannte inkompatible Zeilen filtern und via psql einspielen.
        with tempfile.TemporaryDirectory(prefix="homequests-restore-") as tmp:
            sql_path = Path(tmp) / "restore.sql"
            filtered_sql_path = Path(tmp) / "restore.filtered.sql"

            to_sql_cmd = [
                "pg_restore",
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-privileges",
                "--file",
                str(sql_path),
                str(backup_path),
            ]
            to_sql_result = subprocess.run(
                to_sql_cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_value,
                check=False,
            )
            if to_sql_result.returncode != 0:
                stderr = (to_sql_result.stderr or "").strip()
                safe_stderr = stderr[-400:] if stderr else "unbekannter Fehler"
                raise DbToolsError(f"Restore fehlgeschlagen: {safe_stderr}")

            with sql_path.open("r", encoding="utf-8", errors="ignore") as src, filtered_sql_path.open(
                "w", encoding="utf-8"
            ) as dst:
                for line in src:
                    normalized = line.strip().lower()
                    if normalized == "set transaction_timeout = 0;":
                        continue
                    dst.write(line)

            restore_cmd = [
                "psql",
                "--single-transaction",
                "--set",
                "ON_ERROR_STOP=1",
                "--host",
                host,
                "--port",
                str(port),
                "--username",
                username,
                "--dbname",
                database_name,
                "--file",
                str(filtered_sql_path),
            ]
            result = subprocess.run(
                restore_cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_value,
                check=False,
            )
    except subprocess.TimeoutExpired as exc:
        raise DbToolsError(f"Restore-Timeout nach {timeout_value} Sekunden") from exc
    finally:
        env.pop("PGPASSWORD", None)

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        safe_stderr = stderr[-400:] if stderr else "unbekannter Fehler"
        raise DbToolsError(f"Restore fehlgeschlagen: {safe_stderr}")

    duration = round(time.perf_counter() - started_perf, 3)
    return DbRestoreResult(
        backup_file_path=str(backup_path),
        duration_seconds=duration,
        restored_at_utc=started,
        database_engine=database_engine_name(),
    )
