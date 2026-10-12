from __future__ import annotations

import datetime
import logging
import time

from envoy_collector.config import Settings, get_settings
from envoy_collector.core_api_client import (
    CoreApiError,
    fetch_collector_sources,
    fetch_source_config,
    push_energy_readings,
    report_discovered_config,
)
from envoy_collector.envoy_client import (
    EnvoyAuthError,
    EnvoyCertificateMismatch,
    fetch_battery_power,
    fetch_certificate_fingerprint,
    fetch_production,
    fetch_serial_number,
)
from envoy_collector.mapping import battery_net_w, map_battery_readings, map_production_readings

logger = logging.getLogger("envoy_collector")


def _pin_certificate(settings: Settings, source_id: int, config: dict) -> bool:
    """Trust-on-first-use: pins the Envoy's TLS certificate fingerprint the
    first time this source is seen, so every later request can verify it
    instead of connecting with verification disabled. Returns whether
    `config` now has a usable pin -- poll_once must not send the real token
    to a host that hasn't been pinned yet."""
    if "cert_fingerprint" in config:
        return True
    try:
        fingerprint = fetch_certificate_fingerprint(config["host"])
    except Exception:
        logger.exception("Failed to fetch Envoy TLS certificate for source %s; will retry next refresh", source_id)
        return False
    config["cert_fingerprint"] = fingerprint
    try:
        report_discovered_config(settings.core_api_url, source_id, settings.collector_key, cert_fingerprint=fingerprint)
        logger.info("Pinned Envoy TLS certificate for source %s", source_id)
    except CoreApiError:
        logger.exception("Failed to report pinned TLS certificate to Core API for source %s", source_id)
    return True


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


def _fetch_battery_json_or_none(config: dict) -> dict | None:
    """Battery fetch/parse failures (other than an auth failure, which must
    bubble up so poll_once's shared token-refresh logic can handle it) only
    cost this cycle's battery data, not the whole cycle — a batteryless
    site's /ivp/ensemble/power failing must not also discard production
    data. None (same as a genuinely batteryless site) means _fetch_and_map
    can't apply map_production_readings' battery correction this cycle,
    which just falls back to Envoy's own (battery-unaware) consumption
    figure for one poll — no worse than before this correction existed."""
    try:
        return fetch_battery_power(config["host"], config["token"], config["cert_fingerprint"])
    except EnvoyAuthError:
        raise
    except Exception:
        logger.exception("Failed to fetch or parse battery data this cycle; continuing without it")
        return None


def _fetch_and_map(config: dict, ts: datetime.datetime) -> list[dict]:
    """Fetch and map one cycle's readings. Mapping runs in here (not back in
    poll_once) so a malformed-but-200 Envoy response — e.g. a production.json
    missing the expected "eim" entry — is caught by poll_once's surrounding
    except blocks instead of crashing the process.

    Battery is fetched before production is mapped (not after, as it used to
    be) so its instantaneous power can be threaded into
    map_production_readings — see that function's docstring for why
    Envoy's own consumption figure needs that correction on a site with a
    battery."""
    production = fetch_production(config["host"], config["token"], config["cert_fingerprint"])
    battery_json = _fetch_battery_json_or_none(config)
    battery_readings = map_battery_readings(battery_json, ts) if battery_json is not None else []
    net_w = battery_net_w(battery_json) if battery_json is not None else None
    production_readings = map_production_readings(
        production,
        ts,
        invert_consumption_ct=bool(config.get("invert_consumption_ct")),
        battery_net_w=net_w,
    )
    return production_readings + battery_readings


def poll_once(settings: Settings, source_id: int, config: dict) -> dict:
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
    try:
        readings = _fetch_and_map(config, ts)
    except EnvoyAuthError:
        logger.warning("Envoy rejected our token for source %s; refetching config from Core API", source_id)
        try:
            config = fetch_source_config(settings.core_api_url, source_id, settings.collector_key)
        except CoreApiError:
            logger.exception("Failed to refetch source config after Envoy auth failure for source %s", source_id)
            return config
        try:
            readings = _fetch_and_map(config, ts)
        except EnvoyAuthError:
            logger.error(_renewal_message(config))
            return config
        except Exception:
            logger.exception("Failed to poll Envoy this cycle (after config refetch) for source %s", source_id)
            return config
    except Exception:
        logger.exception("Failed to poll Envoy this cycle for source %s", source_id)
        return config

    try:
        push_energy_readings(settings.core_api_url, source_id, settings.collector_key, readings)
    except CoreApiError:
        logger.exception("Failed to push readings to Core API this cycle for source %s", source_id)
    return config


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
    refreshed: dict[int, dict] = {}
    for entry in discovered:
        source_id = entry["source_id"]
        refreshed[source_id] = entry["config"]
        # Retried every refresh (not just the first time this source_id is
        # seen) so a source whose host was briefly unreachable -- and so
        # never got pinned -- keeps retrying instead of being silently
        # skipped by poll_once for the rest of the process's life.
        if "cert_fingerprint" not in refreshed[source_id]:
            discover_and_sync_serial(settings, source_id, refreshed[source_id])
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

    while True:
        # Sleep first so startup isn't itself a poll trigger.
        time.sleep(settings.poll_interval_seconds)

        now = time.monotonic()
        if now - last_refresh >= settings.discovery_refresh_seconds:
            sources = _refresh_sources(settings, sources)
            last_refresh = now

        for source_id, config in list(sources.items()):
            try:
                sources[source_id] = poll_once(settings, source_id, config)
            except Exception:
                logger.exception("Unexpected error in poll cycle for source %s; continuing", source_id)


if __name__ == "__main__":
    main()
