from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class PricingSource:
    source_id: int


@dataclass(frozen=True)
class Settings:
    core_api_url: str
    collector_key: str


def get_settings() -> Settings:
    core_api_url = os.environ.get("CORE_API_URL")
    collector_key = os.environ.get("COLLECTOR_KEY")
    if not core_api_url or not collector_key:
        raise RuntimeError("CORE_API_URL and COLLECTOR_KEY must both be set")
    return Settings(core_api_url=core_api_url.rstrip("/"), collector_key=collector_key)
