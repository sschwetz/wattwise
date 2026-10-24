from __future__ import annotations

import datetime
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from envoy_collector.config import Settings, get_settings
from envoy_collector.core_api_client import (
    CoreApiError,
    fetch_collector_sources,
    fetch_source_config,
    push_energy_readings,
    push_live_sample,
    report_discovered_config,
)
from envoy_collector.enlighten_client import EnlightenAuthError, EnlightenError, fetch_token, token_expires_at
from envoy_collector.envoy_client import (
    CertificateInfo,
    EnvoyAuthError,
    EnvoyCertificateMismatch,
    fetch_battery_power,
    enable_livedata_stream,
    fetch_certificate_info,
    fetch_livedata,
    fetch_meter_readings,
    fetch_meters,
    fetch_serial_number,
)
from envoy_collector.failure_log import FailureLog
from envoy_collector.live_sampler import LiveSampler
from envoy_collector.mapping import (
    LiveSample,
    battery_net_w,
    map_battery_readings,
    map_live_sample_values,
    map_livedata_readings,
    map_meter_readings,
    map_meter_power_readings,
)

logger = logging.getLogger("envoy_collector")

# Renew when the token has less than this left. Tokens last a year.
RENEWAL_WINDOW = datetime.timedelta(days=14)
# Minimum gap between Enlighten calls for one source, so a wrong password
# can't lock the owner's Enlighten account by retrying every poll.
RENEWAL_RETRY_SECONDS = 3600
_last_renewal_attempt: dict[int, float] = {}
# The Envoy regenerates its self-signed certificate once it is within this
# long of expiring. A changed certificate is only trusted for re-pinning
# inside this window; earlier than that it is more likely someone else
# answering on the Envoy's address. The webapp's SourcesPage.tsx repeats this
# value (CERT_REPIN_WINDOW_DAYS) in its certificate expiry text.
CERT_REPIN_WINDOW = datetime.timedelta(days=100)
# Readings whose push failed, per source, sent again with the next cycle's.
# Core API upserts on (source, ts, metric), so a batch that was in fact
# stored after the timeout is harmless to send twice. Held in memory only: a
# collector restart drops them. Capped so a long outage can't grow without
# bound or produce a batch Core API can't take; the oldest are dropped first.
MAX_PENDING_READINGS = 5000
_pending_readings: dict[int, list[dict]] = {}
# One live-feed sampler per pinned source, and each source's /ivp/meters
# listing (it only changes when hardware is added). Both are held in memory
# only. The meters cache is cleared on an Envoy 401 and on every source
# refresh.
_samplers: dict[int, LiveSampler] = {}
_meters_cache: dict[int, list[dict]] = {}
# Per-source failure streaks for meter reads, so a meter outage logs one
# traceback and then a warning at most every FAILURE_LOG_INTERVAL_SECONDS.
_meter_failure_logs: dict[int, FailureLog] = {}


def _pin_certificate(settings: Settings, source_id: int, config: dict) -> bool:
    """Trust-on-first-use: pins the Envoy's TLS certificate fingerprint the
    first time this source is seen, so every later request can verify it
    instead of connecting with verification disabled. Also records the
    certificate's expiry, which decides whether a later certificate change
    may be re-pinned (see `repin_certificate`). Returns whether `config` now
    has a usable pin -- poll_once must not send the real token to a host
    that hasn't been pinned yet."""
    if "cert_fingerprint" in config:
        _record_cert_details(settings, source_id, config)
        return True
    try:
        info = fetch_certificate_info(config["host"])
    except Exception:
        logger.exception("Failed to fetch Envoy TLS certificate for source %s; will retry next refresh", source_id)
        return False
    _store_pin(settings, source_id, config, info)
    logger.info("Pinned Envoy TLS certificate for source %s", source_id)
    return True


def _cert_details(info: CertificateInfo) -> dict[str, str]:
    return {"cert_expires_at": info.expires_at.isoformat(), "cert_serial": info.serial}


def _store_pin(settings: Settings, source_id: int, config: dict, info: CertificateInfo) -> None:
    details = _cert_details(info)
    config["cert_fingerprint"] = info.fingerprint
    config.update(details)
    try:
        report_discovered_config(
            settings.core_api_url, source_id, settings.collector_key, cert_fingerprint=info.fingerprint, **details
        )
    except CoreApiError:
        logger.exception("Failed to report pinned TLS certificate to Core API for source %s", source_id)


def _record_cert_details(settings: Settings, source_id: int, config: dict) -> None:
    """Backfills `cert_expires_at` and `cert_serial` for a source pinned
    before they were tracked. Only records them when the Envoy still presents
    the pinned certificate, so the stored values always belong to the pinned
    one."""
    if "cert_expires_at" in config and "cert_serial" in config:
        return
    try:
        info = fetch_certificate_info(config["host"])
    except Exception:
        logger.exception("Failed to read the Envoy certificate details for source %s; will retry next refresh", source_id)
        return
    if info.fingerprint != config["cert_fingerprint"]:
        return
    details = _cert_details(info)
    config.update(details)
    try:
        report_discovered_config(settings.core_api_url, source_id, settings.collector_key, **details)
    except CoreApiError:
        logger.exception("Failed to report certificate details to Core API for source %s", source_id)


def _repin_allowed(config: dict, now: datetime.datetime) -> bool:
    raw = config.get("cert_expires_at")
    if not raw:
        return False
    try:
        expires_at = datetime.datetime.fromisoformat(raw)
    except ValueError:
        return False
    return expires_at - now <= CERT_REPIN_WINDOW


def repin_certificate(settings: Settings, source_id: int, config: dict) -> dict:
    """Called when the Envoy presents a certificate other than the pinned
    one. Pins the new certificate only if the pinned one is within
    CERT_REPIN_WINDOW of expiring (the Envoy renews it then) and the device
    reports the same serial number. Otherwise returns `config` unchanged and
    the source keeps failing until the owner clears the pin."""
    known_serial = config.get("serial_num")
    if not known_serial:
        logger.error(
            "Envoy for source %s presented a different TLS certificate than pinned, and no serial number "
            "is stored to check the device against; not re-pinning. Enter the serial number in the source.",
            source_id,
        )
        return config
    if not _repin_allowed(config, datetime.datetime.now(datetime.timezone.utc)):
        logger.error(
            "Envoy for source %s presented a different TLS certificate than pinned, and the pinned one "
            "(expires %s) is not within %s days of expiry; not re-pinning. If the device was replaced, "
            "clear the source's cert_fingerprint.",
            source_id,
            config.get("cert_expires_at", "unknown"),
            CERT_REPIN_WINDOW.days,
        )
        return config
    try:
        info = fetch_certificate_info(config["host"])
        serial = fetch_serial_number(config["host"], info.fingerprint)
    except Exception:
        logger.exception("Failed to read the new Envoy certificate for source %s; will retry next cycle", source_id)
        return config
    if serial != known_serial:
        logger.error(
            "Envoy for source %s now reports serial %s, not %s; not re-pinning its certificate.",
            source_id,
            serial,
            known_serial,
        )
        return config
    new_config = dict(config)
    _store_pin(settings, source_id, new_config, info)
    logger.warning("Re-pinned the renewed Envoy TLS certificate for source %s", source_id)
    return new_config


def discover_and_sync_serial(settings: Settings, source_id: int, config: dict) -> None:
    if not _pin_certificate(settings, source_id, config):
        return
    try:
        serial = fetch_serial_number(config["host"], config["cert_fingerprint"])
    except Exception:
        logger.exception("Failed to fetch Envoy serial number from /info.xml for source %s", source_id)
        return
    if config.get("serial_num") != serial:
        config["serial_num"] = serial
        try:
            report_discovered_config(settings.core_api_url, source_id, settings.collector_key, serial_num=serial)
            logger.info("Reported discovered Envoy serial number %s for source %s", serial, source_id)
        except CoreApiError:
            logger.exception("Failed to report discovered serial number to Core API for source %s", source_id)


def _renewal_message(config: dict) -> str:
    serial_num = config.get("serial_num", "<unknown - check Core API source config>")
    return (
        "Envoy token still invalid after refetching config. Renew it at: "
        f"https://enlighten.enphaseenergy.com/entrez-auth-token?serial_num={serial_num}"
    )


def renew_token(settings: Settings, source_id: int, config: dict) -> dict:
    """Gets a new token from Enlighten and reports it to Core API. Returns
    `config` unchanged when renewal is skipped (missing credentials or serial,
    or tried within the last hour) or fails."""
    username, password, serial_num = config.get("username"), config.get("password"), config.get("serial_num")
    if not (username and password and serial_num):
        return config
    now = time.monotonic()
    last = _last_renewal_attempt.get(source_id)
    if last is not None and now - last < RENEWAL_RETRY_SECONDS:
        return config
    _last_renewal_attempt[source_id] = now
    try:
        token = fetch_token(username, password, serial_num)
    except EnlightenAuthError as exc:
        logger.error("Could not renew the Envoy token for source %s: %s. Update the Enlighten credentials.", source_id, exc)
        return config
    except EnlightenError as exc:
        logger.error("Could not renew the Envoy token for source %s: %s. Will retry in an hour.", source_id, exc)
        return config
    try:
        report_discovered_config(settings.core_api_url, source_id, settings.collector_key, token=token)
    except CoreApiError:
        logger.exception("Renewed the Envoy token for source %s but could not save it to Core API", source_id)
    logger.info("Renewed the Envoy token for source %s", source_id)
    return {**config, "token": token}


def _token_expiring(config: dict, now: datetime.datetime) -> bool:
    expires_at = token_expires_at(config.get("token"))
    return expires_at is not None and expires_at - now < RENEWAL_WINDOW


def _fetch_battery_json_or_none(config: dict) -> dict | None:
    """Battery fetch/parse failures (other than an auth failure, which must
    bubble up so poll_once's shared token-refresh logic can handle it) only
    cost this cycle's battery data, not the whole cycle — a batteryless
    site's /ivp/ensemble/power failing must not also discard the power
    readings. None (same as a genuinely batteryless site) means _fetch_and_map
    can't apply map_meter_power_readings' battery correction this cycle,
    which just falls back to Envoy's own (battery-unaware) consumption
    figure for one poll — no worse than before this correction existed."""
    try:
        return fetch_battery_power(config["host"], config.get("token", ""), config["cert_fingerprint"])
    except EnvoyAuthError:
        raise
    except Exception:
        logger.exception("Failed to fetch or parse battery data this cycle; continuing without it")
        return None


def _cached_meters(source_id: int, config: dict) -> list[dict]:
    """The /ivp/meters listing, read once per source (it only changes when
    hardware is added) and dropped on an Envoy 401 or a source refresh."""
    if source_id not in _meters_cache:
        _meters_cache[source_id] = fetch_meters(config["host"], config.get("token", ""), config["cert_fingerprint"])
    return _meters_cache[source_id]


def _fetch_meter_power(source_id: int, config: dict) -> tuple[list[dict], list[dict]]:
    """(meters, readings) for one power poll."""
    meters = _cached_meters(source_id, config)
    readings = fetch_meter_readings(config["host"], config.get("token", ""), config["cert_fingerprint"])
    return meters, readings


def _fetch_and_map(source_id: int, config: dict, ts: datetime.datetime) -> list[dict]:
    """Fetch and map one cycle's readings. Mapping runs in here (not back in
    poll_once) so a malformed-but-200 Envoy response — e.g. meter readings
    with no net-consumption meter — is caught by poll_once's surrounding
    except blocks instead of crashing the process.

    Battery is fetched before power is mapped so its instantaneous power can
    be threaded into map_meter_power_readings — see that function's docstring
    for why the grid-derived consumption figure needs that correction on a
    site with a battery.

    Both requests are sent at the same time. Every figure is instantaneous
    power, so fetching them one after the other leaves a gap between the two
    samples; the battery correction then subtracts a battery reading from a
    different moment than the consumption figure it corrects, which can push
    house consumption below zero."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        meter_future = pool.submit(_fetch_meter_power, source_id, config)
        battery_future = pool.submit(_fetch_battery_json_or_none, config)
        meters, meter_readings = meter_future.result()
        battery_json = battery_future.result()
    battery_readings = map_battery_readings(battery_json, ts) if battery_json is not None else []
    net_w = battery_net_w(battery_json) if battery_json is not None else None
    power_readings = map_meter_power_readings(
        meters,
        meter_readings,
        ts,
        invert_consumption_ct=bool(config.get("invert_consumption_ct")),
        battery_net_w=net_w,
    )
    return power_readings + battery_readings


def _recover_from_auth_failure(
    settings: Settings, source_id: int, config: dict, ts: datetime.datetime
) -> tuple[dict, list[dict] | None]:
    """Runs after the Envoy answered 401: refetch the config (the token may
    have been edited), then renew from Enlighten if that still fails. Returns
    (config to use next cycle, readings or None if this cycle failed)."""
    logger.warning("Envoy rejected our token for source %s; refetching config from Core API", source_id)
    try:
        config = fetch_source_config(settings.core_api_url, source_id, settings.collector_key)
    except CoreApiError:
        logger.exception("Failed to refetch source config after Envoy auth failure for source %s", source_id)
        return config, None
    try:
        return config, _fetch_and_map(source_id, config, ts)
    except EnvoyAuthError:
        pass
    except Exception:
        logger.exception("Failed to poll Envoy this cycle (after config refetch) for source %s", source_id)
        return config, None
    renewed = renew_token(settings, source_id, config)
    if renewed.get("token") == config.get("token"):
        logger.error(_renewal_message(config))
        return config, None
    try:
        return renewed, _fetch_and_map(source_id, renewed, ts)
    except EnvoyAuthError:
        logger.error(_renewal_message(renewed))
    except Exception:
        logger.exception("Failed to poll Envoy this cycle (after token renewal) for source %s", source_id)
    return renewed, None


def _meter_readings_or_empty(source_id: int, config: dict, ts: datetime.datetime) -> list[dict]:
    """Lifetime energy counters and grid voltages from the Envoy's meters.
    Any failure is logged and gives [] so it never stops the power readings
    from being pushed. The meters listing is read once per source and cached;
    an auth failure clears the cache. Repeated failures are rate-limited
    per source (see FailureLog)."""
    try:
        meters, readings = _fetch_meter_power(source_id, config)
        rows = map_meter_readings(meters, readings, ts)
    except EnvoyAuthError:
        _meters_cache.pop(source_id, None)
        logger.warning("Envoy rejected our token reading meters for source %s", source_id)
        return []
    except Exception:
        _meter_failure_log(source_id).failure()
        return []
    _meter_failure_log(source_id).success()
    return rows


def _meter_failure_log(source_id: int) -> FailureLog:
    """The per-source failure streak for meter reads, created on first use."""
    log = _meter_failure_logs.get(source_id)
    if log is None:
        log = FailureLog(f"Failed to read meter data for source {source_id}; continuing without it", time.monotonic)
        _meter_failure_logs[source_id] = log
    return log


def _live_battery_json(settings: Settings, source_id: int, config: dict) -> dict | None:
    """The /ivp/ensemble/power response for the live path when
    LIVE_BATTERY_SOURCE is "ensemble", else None. Any failure, a rejected
    token included, gives None: this minute then has no battery rows. The
    token is not refreshed from here; the next fallback poll does that."""
    if settings.live_battery_source != "ensemble":
        return None
    try:
        return _fetch_battery_json_or_none(config)
    except EnvoyAuthError:
        logger.warning("Envoy rejected our token reading battery data for source %s; no battery rows this minute", source_id)
        return None


def _live_readings(
    settings: Settings, source_id: int, config: dict, ts: datetime.datetime, sampler: LiveSampler | None
) -> list[dict]:
    """The minute's averaged live-feed readings, or [] when there is no
    sampler, it is stale, or it collected nothing (the first minute after a
    restart). The sampler is drained either way so old samples never pile up."""
    if sampler is None:
        return []
    fresh = sampler.is_fresh()
    samples = sampler.drain()
    if not fresh or not samples:
        return []
    return map_livedata_readings(
        samples,
        ts,
        bool(config.get("invert_consumption_ct")),
        battery_source=settings.live_battery_source,
        battery_json=_live_battery_json(settings, source_id, config),
    )


def _collect_readings(
    settings: Settings, source_id: int, config: dict, ts: datetime.datetime, sampler: LiveSampler | None
) -> tuple[dict, list[dict] | None]:
    """Returns (config to use next cycle, power readings or None if this
    cycle should be skipped). Uses the live feed when it has samples and
    otherwise polls the Envoy's meter and battery endpoints."""
    live = _live_readings(settings, source_id, config, ts, sampler)
    if live:
        return config, live
    try:
        return config, _fetch_and_map(source_id, config, ts)
    except EnvoyAuthError:
        return _recover_from_auth_failure(settings, source_id, config, ts)
    except EnvoyCertificateMismatch:
        return repin_certificate(settings, source_id, config), None
    except Exception:
        logger.exception("Failed to poll Envoy this cycle for source %s", source_id)
        return config, None


def poll_once(settings: Settings, source_id: int, config: dict, sampler: LiveSampler | None = None) -> dict:
    """Runs one poll cycle for one source. Returns the config to use next
    cycle for this source (unchanged, unless an Envoy 401 triggered a
    successful refetch)."""
    if "cert_fingerprint" not in config:
        # Discovery hasn't pinned a certificate for this source yet (new
        # source, or the host was unreachable on a previous refresh) — the
        # real token must never go out to a host that hasn't been pinned.
        # _refresh_sources retries discovery every refresh cycle until this
        # resolves.
        logger.warning(
            "Skipping poll for source %s: no pinned TLS certificate yet for host %s",
            source_id,
            config.get("host"),
        )
        return config
    ts = datetime.datetime.now(datetime.timezone.utc).replace(second=0, microsecond=0)
    config, readings = _collect_readings(settings, source_id, config, ts, sampler)
    if readings is None:
        return config
    readings = readings + _meter_readings_or_empty(source_id, config, ts)

    batch = _pending_readings.pop(source_id, []) + readings
    try:
        push_energy_readings(settings.core_api_url, source_id, settings.collector_key, batch)
    except CoreApiError:
        logger.exception(
            "Failed to push %s readings to Core API this cycle for source %s; will resend next cycle",
            len(batch),
            source_id,
        )
        _pending_readings[source_id] = batch[-MAX_PENDING_READINGS:]
    return config


def _live_pusher(settings: Settings, source_id: int) -> Callable[[LiveSample, dict], None]:
    """Build the on_sample callback that pushes each live sample to Core API."""

    def push(sample: LiveSample, config: dict) -> None:
        values = map_live_sample_values(sample, bool(config.get("invert_consumption_ct")))
        now = datetime.datetime.now(datetime.timezone.utc)
        push_live_sample(settings.core_api_url, source_id, settings.collector_key, now, values)

    return push


def _sync_samplers(settings: Settings, sources: dict[int, dict]) -> None:
    """Starts a live-feed sampler for each source with a pinned certificate,
    hands existing samplers the latest config, and stops samplers for
    sources that are gone or whose pin was cleared."""
    for source_id in list(_samplers):
        if "cert_fingerprint" not in sources.get(source_id, {}):
            _samplers.pop(source_id).stop()
    for source_id, config in sources.items():
        if "cert_fingerprint" not in config:
            continue
        existing = _samplers.get(source_id)
        if existing is not None:
            existing.config = config
            continue
        sampler = LiveSampler(
            config,
            fetch=lambda cfg: fetch_livedata(cfg["host"], cfg.get("token", ""), cfg["cert_fingerprint"]),
            enable_stream=lambda cfg: enable_livedata_stream(
                cfg["host"], cfg.get("token", ""), cfg["cert_fingerprint"]
            ),
            label=f"source {source_id}",
            interval_seconds=settings.live_sample_seconds,
            stale_seconds=settings.live_stale_seconds,
            on_sample=_live_pusher(settings, source_id),
        )
        _samplers[source_id] = sampler
        sampler.start()


def _refresh_sources(settings: Settings, known: dict[int, dict]) -> dict[int, dict]:
    """Fetches the current fleet list and returns an updated {source_id:
    config} dict, always using each source's freshly-discovered config --
    discovery's `config` comes from the same DB row `/sources/{id}/config`
    would return, so carrying over the stale cached config instead would mean
    a source's host/etc edited in the webapp is never picked up by a running
    collector."""
    try:
        discovered = fetch_collector_sources(settings.core_api_url, settings.collector_key)
    except Exception:
        logger.exception("Failed to refresh source list from Core API; keeping last-known sources")
        return known
    _meters_cache.clear()
    refreshed: dict[int, dict] = {}
    for entry in discovered:
        source_id = entry["source_id"]
        refreshed[source_id] = entry["config"]
        # Retried every refresh (not just the first time this source_id is
        # seen) so a source whose host was briefly unreachable -- and so
        # never got pinned -- keeps retrying instead of being silently
        # skipped by poll_once for the rest of the process's life.
        if not {"cert_fingerprint", "cert_expires_at", "cert_serial"} <= refreshed[source_id].keys():
            discover_and_sync_serial(settings, source_id, refreshed[source_id])
        if _token_expiring(refreshed[source_id], datetime.datetime.now(datetime.timezone.utc)):
            refreshed[source_id] = renew_token(settings, source_id, refreshed[source_id])
    return refreshed


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    sources: dict[int, dict] = {}
    while not sources:
        sources = _refresh_sources(settings, sources)
        if not sources:
            logger.warning("No energy sources discovered yet; retrying in %s seconds", settings.poll_interval_seconds)
            time.sleep(settings.poll_interval_seconds)
    last_refresh = time.monotonic()
    _sync_samplers(settings, sources)

    while True:
        # Sleep first so startup isn't itself a poll trigger.
        time.sleep(settings.poll_interval_seconds)

        now = time.monotonic()
        if now - last_refresh >= settings.discovery_refresh_seconds:
            sources = _refresh_sources(settings, sources)
            last_refresh = now
            _sync_samplers(settings, sources)

        for source_id, config in list(sources.items()):
            try:
                sampler = _samplers.get(source_id)
                sources[source_id] = poll_once(settings, source_id, config, sampler)
                if sampler is not None:
                    sampler.config = sources[source_id]
            except Exception:
                logger.exception("Unexpected error in poll cycle for source %s; continuing", source_id)


if __name__ == "__main__":
    main()
