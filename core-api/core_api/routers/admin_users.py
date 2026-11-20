from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core_api import admin_users, user_profile
from core_api.admin_schemas import AdminSiteGrantIn, AdminUserOut, AdminUserPatch
from core_api.deps import get_db_session, get_email_sender, require_admin
from core_api.email_sender import EmailSender
from core_api.errors import AppError
from core_api.models.identity import User
from core_api.routers.password_reset import issue_password_reset
from core_api.schemas import UpdateAmberApiKeyIn

router = APIRouter(prefix="/admin/users", tags=["admin"], dependencies=[Depends(require_admin)])


def _commit_and_report(db: Session, admin: User, user: User, action: str) -> AdminUserOut:
    """Every write route ends here: commit, log the action, return the user."""
    db.commit()
    db.refresh(user)
    admin_users.log_admin_action(admin, user.id, action)
    return admin_users.admin_user_out(db, user)


@router.get("", response_model=list[AdminUserOut])
def list_users(db: Session = Depends(get_db_session)) -> list[AdminUserOut]:
    return admin_users.list_admin_users(db)


@router.get("/{user_id}", response_model=AdminUserOut)
def get_user(user_id: int, db: Session = Depends(get_db_session)) -> AdminUserOut:
    return admin_users.admin_user_out(db, admin_users.get_user_or_404(db, user_id))


@router.patch("/{user_id}", response_model=AdminUserOut)
def update_user(
    user_id: int,
    payload: AdminUserPatch,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db_session),
) -> AdminUserOut:
    user = admin_users.get_user_or_404(db, user_id)
    admin_users.update_user(db, user, payload)
    try:
        return _commit_and_report(db, admin, user, "update_user")
    except IntegrityError as exc:
        # Another request took the address between the check and this commit.
        db.rollback()
        raise AppError(409, "email_taken", "An account with this email already exists") from exc


@router.delete("/{user_id}/avatar", response_model=AdminUserOut)
def remove_avatar(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db_session),
) -> AdminUserOut:
    user = admin_users.get_user_or_404(db, user_id)
    user_profile.remove_avatar(user)
    return _commit_and_report(db, admin, user, "remove_avatar")


@router.put("/{user_id}/amber-key", response_model=AdminUserOut)
def set_amber_api_key(
    user_id: int,
    payload: UpdateAmberApiKeyIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db_session),
) -> AdminUserOut:
    user = admin_users.get_user_or_404(db, user_id)
    user_profile.set_amber_api_key(user, payload.amber_api_key)
    return _commit_and_report(db, admin, user, "set_amber_api_key")


@router.delete("/{user_id}/amber-key", response_model=AdminUserOut)
def clear_amber_api_key(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db_session),
) -> AdminUserOut:
    user = admin_users.get_user_or_404(db, user_id)
    user_profile.set_amber_api_key(user, None)
    return _commit_and_report(db, admin, user, "clear_amber_api_key")


@router.put("/{user_id}/sites/{site_id}", response_model=AdminUserOut)
def grant_site_access(
    user_id: int,
    site_id: int,
    payload: AdminSiteGrantIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db_session),
) -> AdminUserOut:
    user = admin_users.get_user_or_404(db, user_id)
    admin_users.grant_site_access(db, user, site_id, payload.role)
    return _commit_and_report(db, admin, user, "grant_site_access")


@router.delete("/{user_id}/sites/{site_id}", response_model=AdminUserOut)
def remove_site_access(
    user_id: int,
    site_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db_session),
) -> AdminUserOut:
    user = admin_users.get_user_or_404(db, user_id)
    admin_users.remove_site_access(db, admin, user, site_id)
    return _commit_and_report(db, admin, user, "remove_site_access")


@router.post("/{user_id}/password-reset", status_code=status.HTTP_204_NO_CONTENT)
def send_password_reset(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db_session),
    email_sender: EmailSender = Depends(get_email_sender),
) -> None:
    user = admin_users.get_user_or_404(db, user_id)
    issue_password_reset(db, user, email_sender)
    admin_users.log_admin_action(admin, user.id, "password_reset")


@router.post("/{user_id}/totp/reset", response_model=AdminUserOut)
def reset_totp(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db_session),
) -> AdminUserOut:
    user = admin_users.get_user_or_404(db, user_id)
    admin_users.reset_totp(db, user)
    return _commit_and_report(db, admin, user, "reset_totp")
