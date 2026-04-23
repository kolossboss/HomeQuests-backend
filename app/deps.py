from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .database import get_db
from .models import User
from .security import decode_access_token

def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user_from_token_value(token: str, db: Session) -> User:
    try:
        payload = decode_access_token(token)
    except ValueError as exc:
        raise _unauthorized("Ungültiges Token") from exc

    user_id = payload.get("sub")
    if not user_id:
        raise _unauthorized("Token ohne Benutzerkontext")

    try:
        numeric_user_id = int(user_id)
    except (TypeError, ValueError) as exc:
        raise _unauthorized("Token ohne gültige Benutzer-ID") from exc

    user = db.query(User).filter(User.id == numeric_user_id).first()
    if not user or not user.is_active:
        raise _unauthorized("Benutzer nicht gefunden oder deaktiviert")
    return user


def _extract_token_from_request(request: Request) -> str:
    auth_header = (request.headers.get("Authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        header_token = auth_header[7:].strip()
        if header_token:
            return header_token

    cookie_token = (request.cookies.get("fp_token") or "").strip()
    if cookie_token:
        return cookie_token

    raise _unauthorized("Token fehlt")


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = _extract_token_from_request(request)
    return get_current_user_from_token_value(token, db)
