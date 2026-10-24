from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from core_api.deps import require_source_kind
from core_api.live import LiveSample
from core_api.models.sources import Source, SourceKind
from core_api.schemas import EnergyLiveSampleIn, WeatherLiveSampleIn

router = APIRouter(prefix="/sources/{source_id}/live", tags=["live"])


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
