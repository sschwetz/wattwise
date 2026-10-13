from __future__ import annotations

import datetime
import hashlib
import http.client
import json
import ssl
from dataclasses import dataclass

import defusedxml.ElementTree as ET
from cryptography import x509


class EnvoyAuthError(Exception):
    """Raised when the Envoy rejects our current token (HTTP 401)."""


class EnvoyCertificateMismatch(Exception):
    """Raised when the Envoy's TLS certificate doesn't match the fingerprint
    pinned for this source. Either the device was swapped/re-provisioned (a
    legitimate reason to re-pin, via Core API) or something else is
    answering on this host/port -- refusing to send the token either way."""


def _split_host_port(host: str) -> tuple[str, int]:
    if ":" in host:
        hostname, _, port = host.rpartition(":")
        return hostname, int(port)
    return host, 443


def _unverified_context() -> ssl.SSLContext:
    # The Envoy's TLS certificate is self-signed, so there is no CA to
    # validate it against -- that's what makes this a trust-on-first-use
    # pinning scheme rather than ordinary CA-validated TLS. Every connection
    # still has its certificate checked, just against the fingerprint pinned
    # for this source (see `_connect`) instead of a CA chain.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


# Built once at import time: this configuration never varies per connection
# (always unverified, always TOFU-pinned at the application layer), so
# there's no reason to rebuild it on every poll.
_UNVERIFIED_CONTEXT = _unverified_context()


def _connect(host: str) -> http.client.HTTPSConnection:
    hostname, port = _split_host_port(host)
    conn = http.client.HTTPSConnection(hostname, port, timeout=10, context=_UNVERIFIED_CONTEXT)
    conn.connect()
    return conn


def _peer_cert_fingerprint(conn: http.client.HTTPSConnection) -> str:
    cert = conn.sock.getpeercert(binary_form=True)  # type: ignore[union-attr]
    return hashlib.sha256(cert).hexdigest()


def fetch_certificate_fingerprint(host: str) -> str:
    """Connects once, unverified, and returns the SHA-256 fingerprint of the
    Envoy's TLS certificate -- the value to pin on the source's config. This
    single call is the trust-on-first-use moment: every connection after it
    goes through `_request`, which refuses to proceed unless the server
    presents exactly this fingerprint."""
    conn = _connect(host)
    try:
        return _peer_cert_fingerprint(conn)
    finally:
        conn.close()


@dataclass(frozen=True)
class CertificateInfo:
    fingerprint: str
    expires_at: datetime.datetime


def fetch_certificate_info(host: str) -> CertificateInfo:
    """Like `fetch_certificate_fingerprint`, but also returns the
    certificate's expiry (UTC). The Envoy regenerates its certificate once
    it is within 100 days of expiring, so the expiry tells the collector
    when a changed certificate is expected rather than suspicious."""
    conn = _connect(host)
    try:
        der = conn.sock.getpeercert(binary_form=True)  # type: ignore[union-attr]
    finally:
        conn.close()
    expires_at = x509.load_der_x509_certificate(der).not_valid_after_utc
    return CertificateInfo(fingerprint=hashlib.sha256(der).hexdigest(), expires_at=expires_at)


def _request(
    host: str, path: str, cert_fingerprint: str, headers: dict[str, str]
) -> tuple[int, bytes]:
    conn = _connect(host)
    try:
        actual = _peer_cert_fingerprint(conn)
        if actual != cert_fingerprint:
            raise EnvoyCertificateMismatch(
                f"Envoy at {host} presented a different TLS certificate than pinned "
                f"(expected {cert_fingerprint}, got {actual}) -- refusing to send the request"
            )
        conn.request("GET", path, headers=headers)
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def _raise_for_status(status: int, path: str) -> None:
    if status >= 400:
        raise ValueError(f"Envoy returned {status} for {path}")


def _envoy_get(host: str, token: str, path: str, cert_fingerprint: str) -> dict:
    status, body = _request(host, path, cert_fingerprint, {"Authorization": f"Bearer {token}"})
    if status == 401:
        raise EnvoyAuthError(f"Envoy rejected the token for {path}")
    _raise_for_status(status, path)
    return json.loads(body)


def fetch_production(host: str, token: str, cert_fingerprint: str) -> dict:
    return _envoy_get(host, token, "/production.json", cert_fingerprint)


def fetch_battery_power(host: str, token: str, cert_fingerprint: str) -> dict:
    return _envoy_get(host, token, "/ivp/ensemble/power", cert_fingerprint)


def fetch_serial_number(host: str, cert_fingerprint: str) -> str:
    status, body = _request(host, "/info.xml", cert_fingerprint, {})
    _raise_for_status(status, "/info.xml")
    root = ET.fromstring(body)
    serial = root.findtext("device/sn")
    if not serial:
        raise ValueError("/info.xml response did not contain a device serial number")
    return serial
