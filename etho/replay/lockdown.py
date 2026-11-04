"""Demo mode cannot reach a model, a key or the network, whatever the code under it does.

`lockdown()` is a context manager for the demo process (and for tests of it). While it is active:

* the provider API keys are removed from the environment and `ETHO_SECRETS` points at a file that does not exist;
* `etho.gateway.Gateway` cannot be constructed or used (`__init__` and `_request` raise `RealGatewayBlocked`), so a
  code path that builds the live gateway by mistake (the app's default `gateway_factory`) fails at once;
* the real HTTP transport (`httpx.HTTPTransport`) and every non-loopback socket connection are refused.

Nothing under `etho/gateway/` is edited: the guards are installed on the running process and removed on exit.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from contextlib import contextmanager

NO_SECRETS = "/nonexistent/etho-replay-no-secrets.env"
KEY_VARS = ("META_MODEL_API_KEY", "BASETEN_API_KEY", "FIREWORKS_API_KEY", "TOGETHER_API_KEY")


class RealGatewayBlocked(RuntimeError):
    """The demo tried to use the live gateway or the network."""


def _msg(what: str) -> str:
    return (f"{what} is blocked in replay mode: the demo serves recorded responses only, with no keys and no "
            "network. For live results, add your own Meta Model API key (README, Setup) and run without `make demo`.")


def _local(address) -> bool:
    """Only a literal loopback IP or the name `localhost` (never another name: `127.evil.example` is a hostname)."""
    if isinstance(address, (str, bytes)):          # AF_UNIX path
        return True
    host = str(address[0]) if address else ""
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback


@contextmanager
def lockdown():
    import httpx

    from etho.gateway import gateway as gwmod

    saved_env = {k: os.environ.get(k) for k in (*KEY_VARS, "ETHO_SECRETS")}
    for k in KEY_VARS:
        os.environ.pop(k, None)
    os.environ["ETHO_SECRETS"] = NO_SECRETS

    real_init, real_request = gwmod.Gateway.__init__, gwmod.Gateway._request
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex
    real_handle = httpx.HTTPTransport.handle_request

    def no_gateway(*_a, **_k):
        raise RealGatewayBlocked(_msg("The live gateway"))

    def no_http(*_a, **_k):
        raise RealGatewayBlocked(_msg("An HTTP request"))

    def guarded(fn):
        def connect(self, address, *a, **k):
            if not _local(address):
                raise RealGatewayBlocked(_msg(f"A network connection to {address!r}"))
            return fn(self, address, *a, **k)
        return connect

    gwmod.Gateway.__init__ = no_gateway             # type: ignore[method-assign]
    gwmod.Gateway._request = no_gateway             # type: ignore[method-assign]
    httpx.HTTPTransport.handle_request = no_http    # type: ignore[method-assign]
    socket.socket.connect = guarded(real_connect)         # type: ignore[method-assign]
    socket.socket.connect_ex = guarded(real_connect_ex)   # type: ignore[method-assign]
    try:
        yield
    finally:
        gwmod.Gateway.__init__, gwmod.Gateway._request = real_init, real_request   # type: ignore[method-assign]
        httpx.HTTPTransport.handle_request = real_handle                            # type: ignore[method-assign]
        socket.socket.connect, socket.socket.connect_ex = real_connect, real_connect_ex   # type: ignore[method-assign]
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
