from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    core_api_url: str
    # MAIL_ACTOR_KEY, its own key: the device actors' ACTOR_KEY does not open the mail routes.
    mail_actor_key: str = field(repr=False)


def get_settings() -> Settings:
    core_api_url = os.environ.get("CORE_API_URL")
    mail_actor_key = os.environ.get("MAIL_ACTOR_KEY")
    if not core_api_url or not mail_actor_key:
        raise RuntimeError("CORE_API_URL and MAIL_ACTOR_KEY must both be set")
    return Settings(core_api_url=core_api_url.rstrip("/"), mail_actor_key=mail_actor_key)
