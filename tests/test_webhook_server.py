"""Gercek yerel soketlerle webhook dinleyici yasam dongusu kontrolleri."""
import errno
import http.client
import json
import socket
from unittest.mock import patch

import pytest

from app.webhook_server import WebhookServer


def _health(port):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", "/health")
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_start_is_idempotent_and_existing_listener_stays_healthy():
    webhook = WebhookServer("127.0.0.1", 0)
    try:
        assert webhook.start()
        server, thread = webhook._server, webhook._thread
        port = server.server_address[1]
        assert webhook.start()
        assert webhook._server is server
        assert webhook._thread is thread
        assert _health(port) == (200, {"status": "ok"})
        assert webhook.last_error == webhook.error_kind == ""
    finally:
        webhook.stop()


def test_stop_releases_socket_and_same_port_can_restart():
    webhook = WebhookServer("127.0.0.1", 0)
    try:
        assert webhook.start()
        server, thread = webhook._server, webhook._thread
        port = server.server_address[1]
        assert _health(port)[0] == 200
        webhook.stop()
        webhook.stop()
        assert not thread.is_alive()
        assert server.socket.fileno() == -1
        webhook.port = port
        assert webhook.start(), webhook.last_error
        assert webhook._server.server_address[1] == port
        assert _health(port) == (200, {"status": "ok"})
    finally:
        webhook.stop()


def test_busy_port_is_reported_and_retry_uses_the_configured_port():
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.bind(("127.0.0.1", 0))
    occupied.listen()
    port = occupied.getsockname()[1]
    webhook = WebhookServer("127.0.0.1", port)
    try:
        assert not webhook.start()
        assert webhook.error_kind == "port_in_use"
        assert str(port) in webhook.last_error
        assert webhook.port == port
        assert webhook._server is None
        occupied.close()
        assert webhook.start(), webhook.last_error
        assert webhook.last_error == webhook.error_kind == ""
        assert webhook._server.server_address[1] == port
        assert _health(port)[0] == 200
    finally:
        occupied.close()
        webhook.stop()


def test_nonlocal_bind_address_is_not_reported_as_a_busy_port():
    webhook = WebhookServer("203.0.113.253", 0)
    try:
        assert not webhook.start()
        assert webhook.error_kind == "address_unavailable"
        assert "203.0.113.253" in webhook.last_error
        assert webhook._server is None
    finally:
        webhook.stop()


@pytest.mark.parametrize("error, kind", [
    (OSError(errno.EACCES, "access denied"), "permission_denied"),
    (OSError(errno.ENOBUFS, "no socket buffers"), "startup_failed"),
])
def test_other_bind_errors_keep_their_reason_and_close_the_socket(error, kind):
    webhook = WebhookServer("127.0.0.1", 0)
    bound_servers = []

    def fail_bind(server):
        bound_servers.append(server)
        raise error

    with patch("app.webhook_server._ExclusiveHTTPServer.server_bind", fail_bind):
        assert not webhook.start()
    assert webhook.error_kind == kind
    assert str(error) in webhook.last_error
    assert bound_servers[0].socket.fileno() == -1
    assert webhook._server is None
    webhook.stop()


def test_invalid_port_is_reported_without_leaking_a_listener():
    webhook = WebhookServer("127.0.0.1", 65536)
    assert not webhook.start()
    assert webhook.error_kind == "invalid_configuration"
    assert webhook._server is None
    webhook.stop()


def test_second_webhook_cannot_share_an_existing_webhook_port():
    first = WebhookServer("127.0.0.1", 0)
    second = None
    try:
        assert first.start()
        port = first._server.server_address[1]
        second = WebhookServer("127.0.0.1", port)
        assert not second.start()
        assert second.error_kind in ("port_in_use", "permission_denied")
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            assert first._server.socket.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE) == 1
        assert _health(port)[0] == 200
    finally:
        if second is not None:
            second.stop()
        first.stop()
