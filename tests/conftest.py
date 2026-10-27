"""Global Pytest Configuration and Offline Isolation Guard.

Enforces offline execution safety across all test sessions by intercepting and
halting in-process standard library network calls (socket, urllib, http.client).
Preserves and restores original functions during session teardown.

NOTE ON SCOPE: This guard covers in-process Python standard library networking.
It does not and cannot intercept raw sockets created by external child processes
(e.g., msedge.exe, node.exe) or native C libraries bypassing socket.py. Subprocess
tests must remain quarantined in the host_integration suite.
"""

from __future__ import annotations

import http.client
import socket
import urllib.request
import pytest


@pytest.fixture(autouse=True, scope="session")
def enforce_global_offline_isolation():
    """Intercepts and halts all in-process standard library network socket creation."""
    orig_socket_connect = socket.socket.connect
    orig_urlopen = urllib.request.urlopen
    orig_http_connect = http.client.HTTPConnection.connect
    orig_https_connect = http.client.HTTPSConnection.connect

    def blocked_network(*args, **kwargs):
        raise RuntimeError("CRITICAL: Prohibited live network call attempted in offline test suite!")

    socket.socket.connect = blocked_network
    urllib.request.urlopen = blocked_network
    http.client.HTTPConnection.connect = blocked_network
    http.client.HTTPSConnection.connect = blocked_network

    yield

    # Restore original functions during session teardown
    socket.socket.connect = orig_socket_connect
    urllib.request.urlopen = orig_urlopen
    http.client.HTTPConnection.connect = orig_http_connect
    http.client.HTTPSConnection.connect = orig_https_connect
