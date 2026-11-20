from __future__ import annotations

from pydantic import BaseModel

from core_api.models.identity import SiteRole


class AdminUserSite(BaseModel):
    site_id: int
    site_name: str
    role: SiteRole


class AdminUserOut(BaseModel):
    """A user as an administrator sees them. Secrets appear only as has_* flags."""

    id: int
    email: str
    display_name: str | None
    is_admin: bool
    has_avatar: bool
    totp_enabled: bool
    email_confirmed: bool
    has_amber_api_key: bool
    sites: list[AdminUserSite]
