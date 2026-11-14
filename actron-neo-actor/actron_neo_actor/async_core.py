"""CoreApiClient calls run in a worker thread so a slow Core API never stalls the
event loop that carries the device's push callbacks."""

from __future__ import annotations

import asyncio
import datetime

from actron_neo_actor.core_api_client import CoreApiClient
from actron_neo_actor.models import Context, Instance, Run, Schedule


class AsyncCore:
    def __init__(self, client: CoreApiClient) -> None:
        self._client = client

    @property
    def sync(self) -> CoreApiClient:
        """The wrapped client, for auth.ensure_session, which calls it directly."""
        return self._client

    async def list_instances(self) -> list[Instance]:
        return await asyncio.to_thread(self._client.list_instances)

    async def put_capabilities(self, instance_id: int, capabilities: dict) -> None:
        await asyncio.to_thread(self._client.put_capabilities, instance_id, capabilities)

    async def set_alert(
        self, instance_id: int, code: str, message: str, severity: str = "failure", sticky: bool = False
    ) -> None:
        await asyncio.to_thread(self._client.set_alert, instance_id, code, message, severity, sticky)

    async def clear_alert(self, instance_id: int, code: str) -> None:
        await asyncio.to_thread(self._client.clear_alert, instance_id, code)

    async def get_schedules(self, instance_id: int) -> list[Schedule]:
        return await asyncio.to_thread(self._client.get_schedules, instance_id)

    async def get_context(self, instance_id: int) -> Context:
        return await asyncio.to_thread(self._client.get_context, instance_id)

    async def put_readings(self, instance_id: int, state: dict) -> None:
        await asyncio.to_thread(self._client.put_readings, instance_id, state)

    async def create_run(
        self,
        instance_id: int,
        schedule_id: int,
        kind: str,
        trigger_at: datetime.datetime,
        parent_run_id: int | None = None,
    ) -> Run:
        return await asyncio.to_thread(
            self._client.create_run, instance_id, schedule_id, kind, trigger_at, parent_run_id
        )

    async def update_run(
        self,
        instance_id: int,
        run_id: int,
        outcome: str,
        message: str | None = None,
        attempts: int | None = None,
        snapshot: dict | None = None,
        steps: list[dict] | None = None,
    ) -> Run:
        return await asyncio.to_thread(
            self._client.update_run, instance_id, run_id, outcome, message, attempts, snapshot, steps
        )

    async def list_unfinished_runs(self, instance_id: int) -> list[Run]:
        return await asyncio.to_thread(self._client.list_unfinished_runs, instance_id)

    async def list_pending_test_runs(self, instance_id: int) -> list[Run]:
        return await asyncio.to_thread(self._client.list_pending_test_runs, instance_id)

    async def list_recent_runs(self, instance_id: int, since: datetime.datetime) -> list[Run]:
        return await asyncio.to_thread(self._client.list_recent_runs, instance_id, since)
