from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core_api import admin_users
from core_api.admin_schemas import AdminUserOut
from core_api.deps import get_db_session, require_admin
from core_api.errors import AppError
from core_api.models.identity import User

router = APIRouter(prefix="/admin/users", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("", response_model=list[AdminUserOut])
def list_users(db: Session = Depends(get_db_session)) -> list[AdminUserOut]:
    return admin_users.list_admin_users(db)


@router.get("/{user_id}", response_model=AdminUserOut)
def get_user(user_id: int, db: Session = Depends(get_db_session)) -> AdminUserOut:
    user = db.get(User, user_id)
    if user is None:
        raise AppError(404, "not_found", f"User {user_id} not found")
    return admin_users.admin_user_out(db, user)
