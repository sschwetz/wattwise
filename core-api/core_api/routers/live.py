from __future__ import annotations

import asyncio
import json

import jwt as pyjwt
from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect, status

from core_api.deps import require_source_kind, site_visible_to
from core_api.live import LiveSample, LiveSubscription
from core_api.models.sources import Source, SourceKind
from core_api.schemas import EnergyLiveSampleIn, WeatherLiveSampleIn
from core_api.security import decode_access_token

LIVE_AUTH_TIMEOUT_SECONDS = 5

router = APIRouter(prefix="/sources/{source_id}/live", tags=["live"])
socket_router = APIRouter(tags=["live"])


def _publish(request: Request, source: Source, body: EnergyLiveSampleIn | WeatherLiveSampleIn) -> dict:
    sample = LiveSample(source_id=source.id, kind=source.kind, ts=body.ts, values=body.values)
    request.app.state.live_hub.publish(source.site_id, sample)
    return {}


# These are async so they run on the event loop: LiveHub's queues are not thread-safe.
# They never write to the database.
@router.post("/energy", status_code=status.HTTP_202_ACCEPTED)
async def push_energy_live(
    body: EnergyLiveSampleIn, request: Request, source: Source = Depends(require_source_kind(SourceKind.ENERGY))
) -> dict:
    return _publish(request, source, body)


@router.post("/weather", status_code=status.HTTP_202_ACCEPTED)
async def push_weather_live(
    body: WeatherLiveSampleIn, request: Request, source: Source = Depends(require_source_kind(SourceKind.WEATHER))
) -> dict:
    return _publish(request, source, body)


def _message(sample: LiveSample) -> dict:
    return {"kind": sample.kind.value, "ts": sample.ts.isoformat(), "values": sample.values}


async def _read_token(websocket: WebSocket) -> int | None:
    """User id from the first message, `{"token": "<jwt>"}`, or None if it is late or malformed."""
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), LIVE_AUTH_TIMEOUT_SECONDS)
        token = json.loads(raw)["token"]
        return decode_access_token(token)
    except (asyncio.TimeoutError, WebSocketDisconnect, ValueError, KeyError, TypeError, pyjwt.InvalidTokenError):
        return None


async def _authenticate(websocket: WebSocket, site_id: int) -> bool:
    user_id = await _read_token(websocket)
    if user_id is None:
        return False
    db = websocket.app.state.session_factory()
    try:
        return site_visible_to(db, site_id, user_id) is not None
    finally:
        db.close()


async def _pump(websocket: WebSocket, subscription: LiveSubscription, current: list[LiveSample]) -> None:
    for sample in current:
        await websocket.send_json(_message(sample))
    while True:
        await websocket.send_json(_message(await subscription.queue.get()))


async def _watch_disconnect(websocket: WebSocket) -> None:
    """Returns when the client goes away. The client sends nothing after auth."""
    while True:
        await websocket.receive_text()


async def _forward_until_disconnect(
    websocket: WebSocket, subscription: LiveSubscription, current: list[LiveSample]
) -> None:
    pump = asyncio.create_task(_pump(websocket, subscription, current))
    watch = asyncio.create_task(_watch_disconnect(websocket))
    done, pending = await asyncio.wait({pump, watch}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in done:
        exc = task.exception()
        if exc is not None and not isinstance(exc, WebSocketDisconnect):
            raise exc


@socket_router.websocket("/sites/{site_id}/live")
async def live_socket(websocket: WebSocket, site_id: int) -> None:
    await websocket.accept()
    if not await _authenticate(websocket, site_id):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    hub = websocket.app.state.live_hub
    # Subscribe before reading the stored samples so a push in between is not lost.
    subscription = hub.subscribe(site_id)
    try:
        await _forward_until_disconnect(websocket, subscription, hub.latest_for_site(site_id))
    finally:
        subscription.close()
