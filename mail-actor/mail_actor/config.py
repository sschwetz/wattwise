from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    core_api_url: str
    actor_key: str = field(repr=False)


def get_settings() -> Settings:
    core_api_url = os.environ.get("CORE_API_URL")
    actor_key = os.environ.get("ACTOR_KEY")
    if not core_api_url or not actor_key:
        raise RuntimeError("CORE_API_URL and ACTOR_KEY must both be set")
    return Settings(core_api_url=core_api_url.rstrip("/"), actor_key=actor_key)
