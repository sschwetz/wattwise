from __future__ import annotations

import os
from dataclasses import dataclass


def _require_env(name: str, *, insecure_default_note: str = "") -> str:
    value = os.environ.get(name)
    if not value:
        if insecure_default_note:
            raise RuntimeError(f"{name} environment variable must be set{insecure_default_note}")
        else:
            raise RuntimeError(f"{name} environment variable must be set.")
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str
    avatar_storage_dir: str
    jwt_secret: str
    secrets_encryption_key: str
    jwt_expiry_hours: int
    smtp_host: str
    smtp_port: int
    smtp_from: str
    smtp_user: str | None
    smtp_password: str | None
    frontend_base_url: str
    scheduler_enabled: bool
    admin_email: str
    envoy_collector_key: str
    weather_collector_key: str
    pricing_collector_key: str


def get_settings() -> Settings:
    return Settings(
        database_url=os.environ.get("DATABASE_URL", "sqlite:///./core_api.db"),
        avatar_storage_dir=os.environ.get("AVATAR_STORAGE_DIR", "./data/avatars"),
        jwt_secret=_require_env("JWT_SECRET", insecure_default_note=" (no insecure default is provided)."),
        secrets_encryption_key=_require_env(
            "SECRETS_ENCRYPTION_KEY",
            insecure_default_note=" (a Fernet key; generate one with: openssl rand -base64 32 | tr '+/' '-_').",
        ),
        jwt_expiry_hours=int(os.environ.get("JWT_EXPIRY_HOURS", "24")),
        smtp_host=os.environ.get("SMTP_HOST", "mail-relay"),
        smtp_port=int(os.environ.get("SMTP_PORT", "25")),
        smtp_from=os.environ.get("SMTP_FROM", "noreply@localhost"),
        smtp_user=os.environ.get("SMTP_USER") or None,
        smtp_password=os.environ.get("SMTP_PASSWORD") or None,
        frontend_base_url=os.environ.get("FRONTEND_BASE_URL", "http://localhost:5173"),
        scheduler_enabled=os.environ.get("SCHEDULER_ENABLED", "true").lower() not in ("0", "false", "no"),
        admin_email=os.environ.get("ADMIN_EMAIL", "admin@example.com"),
        envoy_collector_key=_require_env("ENVOY_COLLECTOR_KEY"),
        weather_collector_key=_require_env("WEATHER_COLLECTOR_KEY"),
        pricing_collector_key=_require_env("PRICING_COLLECTOR_KEY"),
    )
