"""Regression test for the network-blocking autouse fixture.

Guards against the exact failure mode from issue #32: a mock that fails to
intercept a network-calling function silently falling through to a real
outbound call instead of failing fast and obviously.
"""

import socket

import pytest
from pytest_socket import SocketBlockedError


def test_outbound_socket_calls_are_blocked() -> None:
    """A raw outbound connection attempt raises instead of hitting the network.

    This stands in for a mock that fails to intercept a network-calling
    function (e.g. after a refactor retargets the wrong module) - such a
    call must fail fast and obviously, not silently succeed against a real
    external service.
    """
    with pytest.raises(SocketBlockedError):
        socket.create_connection(("huggingface.co", 443), timeout=1)
