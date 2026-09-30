"""Deny network in Python tests; this is not an OS/process sandbox.

Socket audit events cover Python DNS and connections. curl_cffi entry points
cover the native libcurl transport used by yfinance. Mocked providers remain
usable. Other native transports and external executables are outside this
bounded test guard.
"""
from __future__ import annotations

from pathlib import Path
import sys


class NetworkAttempt(AssertionError):
    """An offline test reached an actual transport boundary."""


def install_guard(attempts: list[str], marker: Path | None = None) -> None:
    """Install before application imports; record redacted operation names.

    A marker lets the parent fixture reject caught attempts in guarded children.
    """
    def deny(operation):
        attempts.append(operation)
        if marker is not None:
            with marker.open("a", encoding="utf-8") as stream:
                stream.write(operation + "\n")
        raise NetworkAttempt("offline test blocked network operation: " + operation)

    def audit(event, args):
        if event in {
            "socket.connect", "socket.getaddrinfo", "socket.gethostbyname",
            "socket.gethostbyaddr", "socket.getnameinfo", "socket.sendto", "socket.sendmsg",
        }:
            deny(event)

    sys.addaudithook(audit)
    # Importing this transport performs no requests. Block native libcurl too.
    import curl_cffi
    from curl_cffi import requests

    def blocked(*args, **kwargs):
        deny("curl_cffi.request")

    async def blocked_async(*args, **kwargs):
        deny("curl_cffi.async_request")

    curl_cffi.Curl.perform = blocked
    curl_cffi.AsyncCurl.add_handle = blocked
    requests.Session.request = blocked
    requests.AsyncSession.request = blocked_async


def child_command(command: list[str], marker: Path) -> list[str]:
    """Wrap Python -m/-c without modifying the supplied environment.

    Callers must supply all isolated FIN paths themselves. -I/-S, shell scripts,
    Node, and other native executables are intentionally not wrapped.
    """
    if len(command) < 3 or command[1] not in {"-m", "-c"}:
        raise ValueError("offline_python requires a Python -m or -c command")
    helper = str(Path(__file__).resolve())
    bootstrap = f"""
import atexit, os, runpy, sys
from pathlib import Path
attempts = []
namespace = runpy.run_path({helper!r})
namespace['install_guard'](attempts, Path({str(marker)!r}))
def finish():
    if attempts:
        os.write(2, b'offline child attempted network access\\n')
        os._exit(86)
atexit.register(finish)
mode, target = sys.argv[1:3]
sys.argv = [target if mode == '-m' else '-c', *sys.argv[3:]]
if mode == '-m':
    runpy.run_module(target, run_name='__main__', alter_sys=True)
else:
    exec(compile(target, '<string>', 'exec'), {{'__name__': '__main__'}})
"""
    return [command[0], "-c", bootstrap, *command[1:]]
