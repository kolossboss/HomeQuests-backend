from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from .models import FamilyMembership, RoleEnum


class MembershipContext:
    def __init__(self, membership: FamilyMembership):
        self.membership = membership
        self.role = membership.role


def get_membership_or_403(db: Session, family_id: int, user_id: int) -> MembershipContext:
    membership = (
        db.query(FamilyMembership)
        .filter(FamilyMembership.family_id == family_id, FamilyMembership.user_id == user_id)
        .first()
    )
    if not membership:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Kein Zugriff auf diese Familie")
    return MembershipContext(membership)


def require_roles(context: MembershipContext, allowed: set[RoleEnum]) -> None:
    if context.role not in allowed:
        allowed_roles = ", ".join(sorted(role.value for role in allowed))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Rolle nicht erlaubt. Benötigt: {allowed_roles}",
        )
