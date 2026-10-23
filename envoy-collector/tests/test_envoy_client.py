from __future__ import annotations

import datetime
import hashlib
import http.server
import json
import ssl
import subprocess
import threading

import pytest
from cryptography import x509

from envoy_collector.envoy_client import (
    EnvoyAuthError,
    EnvoyCertificateMismatch,
    fetch_battery_power,
    fetch_certificate_fingerprint,
    enable_livedata_stream,
    fetch_certificate_info,
    fetch_livedata,
    fetch_meter_readings,
    fetch_meters,
    fetch_production,
    fetch_serial_number,
)

VALID_TOKEN = "my-token"

_GET_PAYLOADS: dict[str, dict | list] = {
    "/ivp/livedata/status": {"connection": {"sc_stream": "enabled"}, "meters": {}},
    "/ivp/meters": [{"eid": 1, "state": "enabled"}],
    "/ivp/meters/readings": [{"eid": 1, "activePower": 12.5}],
}
_posted_bodies: list[bytes] = []


class _Handler(http.server.BaseHTTPRequestHandler):
    def _write_json(self, status: int, payload: dict | list) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {VALID_TOKEN}"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's naming convention
        if self.path == "/production.json":
            if not self._authorized():
                self.send_response(401)
                self.end_headers()
                return
            self._write_json(200, {"production": [], "consumption": [], "storage": []})
        elif self.path == "/ivp/ensemble/power":
            if not self._authorized():
                self.send_response(401)
                self.end_headers()
                return
            self._write_json(200, {"devices:": []})
        elif self.path in _GET_PAYLOADS:
            if not self._authorized():
                self.send_response(401)
                self.end_headers()
                return
            self._write_json(200, _GET_PAYLOADS[self.path])
        elif self.path == "/info.xml":
            body = (
                b"<?xml version='1.0'?><envoy_info><device><sn>123456789012</sn>"
                b"</device></envoy_info>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/ivp/livedata/stream":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if not self._authorized():
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        _posted_bodies.append(body)
        self._write_json(200, {"sc_stream": "enabled"})

    def log_message(self, format, *args):  # noqa: A002 - matches base signature
        pass


def _cert_fingerprint(pem_path) -> str:
    der = ssl.PEM_cert_to_DER_cert(pem_path.read_text())
    return hashlib.sha256(der).hexdigest()


@pytest.fixture
def envoy_server(tmp_path):
    """A real local HTTPS server with a throwaway self-signed certificate,
    standing in for an Envoy's local API. respx (used elsewhere in this repo)
    only mocks httpx; cert pinning is only meaningful against a real TLS
    handshake, so this module talks to raw sockets instead and needs a real
    server to test against."""
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(key_path), "-out", str(cert_path),
            "-days", "1", "-nodes", "-subj", "/CN=envoy.local",
        ],
        check=True,
        capture_output=True,
    )

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert_path), str(key_path))
    server.socket = context.wrap_socket(server.socket, server_side=True)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = f"127.0.0.1:{server.server_address[1]}"
        yield host, _cert_fingerprint(cert_path)
    finally:
        server.shutdown()
        server.server_close()


def test_fetch_certificate_fingerprint_matches_the_servers_real_certificate(envoy_server):
    host, expected_fingerprint = envoy_server
    assert fetch_certificate_fingerprint(host) == expected_fingerprint


def test_fetch_certificate_info_returns_fingerprint_and_expiry(envoy_server):
    host, expected_fingerprint = envoy_server
    info = fetch_certificate_info(host)
    assert info.fingerprint == expected_fingerprint
    hostname, _, port = host.rpartition(":")
    pem = ssl.get_server_certificate((hostname, int(port)))
    assert info.serial == format(x509.load_pem_x509_certificate(pem.encode()).serial_number, "X")
    # The fixture certificate is valid for one day.
    remaining = info.expires_at - datetime.datetime.now(datetime.timezone.utc)
    assert datetime.timedelta(hours=23) < remaining <= datetime.timedelta(days=1)
    assert info.expires_at.tzinfo is not None


def test_fetch_production_sends_bearer_token_and_parses_json(envoy_server):
    host, fingerprint = envoy_server
    result = fetch_production(host, VALID_TOKEN, fingerprint)
    assert result == {"production": [], "consumption": [], "storage": []}


def test_fetch_production_raises_envoy_auth_error_on_401(envoy_server):
    host, fingerprint = envoy_server
    with pytest.raises(EnvoyAuthError):
        fetch_production(host, "wrong-token", fingerprint)


def test_fetch_production_refuses_a_connection_with_the_wrong_pinned_fingerprint(envoy_server):
    """The core of the fix: even with the CORRECT token, a mismatched pin
    must stop the request before the token is ever sent -- this is what
    closes the MITM gap that unconditional verify=False left open."""
    host, _real_fingerprint = envoy_server
    wrong_fingerprint = "0" * 64
    with pytest.raises(EnvoyCertificateMismatch):
        fetch_production(host, VALID_TOKEN, wrong_fingerprint)


def test_fetch_battery_power_sends_bearer_token(envoy_server):
    host, fingerprint = envoy_server
    result = fetch_battery_power(host, VALID_TOKEN, fingerprint)
    assert result == {"devices:": []}


def test_fetch_battery_power_refuses_a_connection_with_the_wrong_pinned_fingerprint(envoy_server):
    host, _real_fingerprint = envoy_server
    with pytest.raises(EnvoyCertificateMismatch):
        fetch_battery_power(host, VALID_TOKEN, "0" * 64)


def test_fetch_serial_number_parses_xml(envoy_server):
    host, fingerprint = envoy_server
    assert fetch_serial_number(host, fingerprint) == "123456789012"


def test_fetch_serial_number_refuses_a_connection_with_the_wrong_pinned_fingerprint(envoy_server):
    host, _real_fingerprint = envoy_server
    with pytest.raises(EnvoyCertificateMismatch):
        fetch_serial_number(host, "0" * 64)


@pytest.mark.parametrize(
    "fetch, path",
    [
        (fetch_livedata, "/ivp/livedata/status"),
        (fetch_meters, "/ivp/meters"),
        (fetch_meter_readings, "/ivp/meters/readings"),
    ],
)
class TestLiveGets:
    def test_sends_bearer_token_and_parses_json(self, envoy_server, fetch, path):
        host, fingerprint = envoy_server
        assert fetch(host, VALID_TOKEN, fingerprint) == _GET_PAYLOADS[path]

    def test_raises_auth_error_on_401(self, envoy_server, fetch, path):
        host, fingerprint = envoy_server
        with pytest.raises(EnvoyAuthError):
            fetch(host, "wrong-token", fingerprint)

    def test_refuses_wrong_fingerprint(self, envoy_server, fetch, path):
        host, _ = envoy_server
        with pytest.raises(EnvoyCertificateMismatch):
            fetch(host, VALID_TOKEN, "0" * 64)


def test_enable_livedata_stream_posts_enable_body(envoy_server):
    host, fingerprint = envoy_server
    _posted_bodies.clear()
    assert enable_livedata_stream(host, VALID_TOKEN, fingerprint) is None
    assert [json.loads(b) for b in _posted_bodies] == [{"enable": 1}]


def test_enable_livedata_stream_raises_auth_error_on_401(envoy_server):
    host, fingerprint = envoy_server
    with pytest.raises(EnvoyAuthError):
        enable_livedata_stream(host, "wrong-token", fingerprint)


def test_enable_livedata_stream_refuses_wrong_fingerprint(envoy_server):
    host, _ = envoy_server
    with pytest.raises(EnvoyCertificateMismatch):
        enable_livedata_stream(host, VALID_TOKEN, "0" * 64)
