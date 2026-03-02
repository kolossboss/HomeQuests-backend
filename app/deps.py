from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from .database import get_db
from .models import User
from .security import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


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


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    return get_current_user_from_token_value(token, db)
