from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..models import Family, FamilyMembership, RoleEnum, User
from ..schemas import BootstrapRequest, BootstrapStatusOut, LoginRequest, TokenResponse, UserOut
from ..security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])
COOKIE_NAME = "fp_token"
COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 30


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


@router.get("/bootstrap-status", response_model=BootstrapStatusOut)
def bootstrap_status(db: Session = Depends(get_db)):
    has_user = db.query(User.id).first() is not None
    return BootstrapStatusOut(bootstrap_required=not has_user)


@router.post("/bootstrap", response_model=TokenResponse)
def bootstrap(payload: BootstrapRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    existing = db.query(User).count()
    if existing > 0:
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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Falsche Zugangsdaten")

    token = create_access_token(str(user.id))
    _set_auth_cookie(response, token, request)
    return TokenResponse(access_token=token)


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return {"logged_out": True}


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user
