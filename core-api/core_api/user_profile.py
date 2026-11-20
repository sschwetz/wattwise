"""Profile writes shared by the account router (a user editing themselves) and
the admin users router (an admin editing someone else). Neither commits; the
caller commits once with its other changes."""

from __future__ import annotations

from pathlib import Path

from core_api.config import get_settings
from core_api.models.identity import User


def avatar_path(user_id: int) -> Path:
    """The avatar file for one user. Callers pass a user id they have already
    authorised: the account routes use the caller's own id, and the admin
    routes sit behind require_admin."""
    return Path(get_settings().avatar_storage_dir) / str(user_id)


def remove_avatar(user: User) -> None:
    avatar_path(user.id).unlink(missing_ok=True)
    user.avatar_content_type = None


def set_amber_api_key(user: User, amber_api_key: str | None) -> None:
    """Stores the key in the encrypted column, or clears it with None. Never
    log or return the value; responses report only has_amber_api_key."""
    user.amber_api_key = amber_api_key
