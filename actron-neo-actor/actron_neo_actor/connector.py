"""The production DeviceFactory: one Actron account and one ActronAirAPI per instance."""

from __future__ import annotations

import datetime
import logging
from collections.abc import Callable

from actron_neo_api import ActronAirAPI, ActronAirAPIError, ActronAirAuthError

from actron_neo_actor.async_core import AsyncCore
from actron_neo_actor.auth import SignInState, ensure_session
from actron_neo_actor.config import Settings
from actron_neo_actor.device import ActronDevice, Device, DeviceAuthError, DeviceError, DeviceState
from actron_neo_actor.models import Instance

_LOG = logging.getLogger(__name__)


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


class ActronConnector:
    """Signs in with auth.ensure_session and builds the device for the account's first system.

    ensure_session takes the synchronous CoreApiClient and runs its calls in a thread.
    """

    def __init__(
        self,
        core: AsyncCore,
        settings: Settings,
        *,
        api: ActronAirAPI | None = None,
        clock: Callable[[], datetime.datetime] = _utcnow,
    ) -> None:
        self._core = core
        self._settings = settings
        self._api = api if api is not None else ActronAirAPI()
        self._clock = clock
        self._state: SignInState | None = None

    async def sign_in(self, instance: Instance) -> bool:
        result = await ensure_session(self._api, instance, self._core.sync, self._state, clock=self._clock)
        self._state = result.state
        return result.signed_in

    async def create(self, on_change: Callable[[DeviceState], None]) -> Device:
        try:
            systems = await self._api.get_ac_systems()
        except ActronAirAuthError as err:
            raise DeviceAuthError("listing systems: sign-in rejected") from err
        except (ActronAirAPIError, OSError) as err:
            raise DeviceError(f"listing systems failed: {type(err).__name__}") from err
        if not systems:
            raise DeviceError("no air conditioner on this Actron account")
        return ActronDevice(self._api, systems[0].serial, self._settings, on_change=on_change)

    def signed_out(self) -> None:
        """The cloud rejected the session: check the stored token again on the next sign_in."""
        if self._state is not None:
            self._state.session_token = None

    async def close(self) -> None:
        if self._state is not None:
            self._state.cancel()
        try:
            await self._api.close()
        except Exception as err:  # the client is being discarded either way
            _LOG.warning("Closing the Actron client failed: %s", type(err).__name__)
