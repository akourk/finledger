"""Regression probes use only fictional sentinels and never issue real requests."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests._offline import child_command

ROOT = Path(__file__).resolve().parents[1]


def _run(command, *, env=None):
    return subprocess.run(command, cwd=ROOT, env=env, text=True,
                          capture_output=True, timeout=60)


@pytest.mark.parametrize("probe", [
    "import socket; socket.getaddrinfo('offline-probe.invalid', 443)",
    "import socket; socket.getnameinfo(('192.0.2.1', 443), 0)",
    "import socket; socket.socket().connect(('192.0.2.1', 443))",
    "from curl_cffi import Curl; Curl().perform()",
    "from curl_cffi import requests; requests.get('offline-probe://never')",
])
def test_child_blocks_transport_even_if_exception_is_caught(tmp_path, probe):
    marker = tmp_path / "attempts"
    script = """
import sys
def backstop(event, args):
    if event.startswith('socket.') and event != 'socket.__new__':
        raise RuntimeError('probe backstop forbids all socket activity')
sys.addaudithook(backstop)
""" + "try:\n    " + probe + "\nexcept Exception:\n    pass\n"
    result = _run(child_command([sys.executable, "-c", script], marker))
    assert result.returncode == 86
    assert marker.read_text().strip() in {"socket.getaddrinfo", "socket.getnameinfo", "socket.connect",
                                          "curl_cffi.request"}
    assert "offline child attempted network access" in result.stderr
    assert "offline-probe.invalid" not in result.stderr
    assert "192.0.2.1" not in result.stderr


def test_child_guard_preserves_provider_mock_and_script_arguments(tmp_path):
    marker = tmp_path / "attempts"
    script = """
from unittest.mock import patch
from curl_cffi import requests
import sys
with patch.object(requests.Session, 'request', return_value='fictional'):
    assert requests.get('offline-probe://never') == 'fictional'
assert sys.argv == ['-c', 'fictional-argument']
"""
    result = _run(child_command([sys.executable, "-c", script, "fictional-argument"], marker))
    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_default_pytest_guard_rejects_caught_direct_and_child_attempts(tmp_path):
    # Load the real fixture definitions into an independent, tiny pytest run.
    # Expected failures are confined to the child and cannot pollute this test's
    # process-wide audit hook or its fixture attempt accounting.
    (tmp_path / "conftest.py").write_text(
        f"import runpy\nnamespace = runpy.run_path({str(ROOT / 'tests/conftest.py')!r})\n"
        "globals().update({key: value for key, value in namespace.items() if not key.startswith('__')})\n")
    (tmp_path / "test_probe.py").write_text("""
import socket, subprocess, sys

def backstop(event, args):
    if event.startswith('socket.') and event != 'socket.__new__':
        raise RuntimeError('probe backstop forbids all socket activity')
sys.addaudithook(backstop)

def test_caught_direct():
    try:
        socket.getaddrinfo('offline-probe.invalid', 443)
    except Exception:
        pass

def test_caught_child(offline_python):
    script = "import sys\\nsys.addaudithook(lambda event, args: (_ for _ in ()).throw(RuntimeError('probe backstop')) if event.startswith('socket.') else None)\\ntry:\\n import socket; socket.getaddrinfo('offline-probe.invalid', 443)\\nexcept Exception:\\n pass"
    result = subprocess.run(offline_python([sys.executable, '-c', script]), capture_output=True)
    assert result.returncode == 86

def test_caught_native():
    from curl_cffi import requests
    try:
        requests.get('offline-probe://never')
    except Exception:
        pass
""")
    result = _run([sys.executable, "-m", "pytest", str(tmp_path), "-q",
                   "-p", "no:cacheprovider"])
    assert result.returncode != 0
    assert "3 passed, 3 errors" in result.stdout
    assert "offline test attempted network access: socket.getaddrinfo" in result.stdout
    assert "offline child attempted network access: socket.getaddrinfo" in result.stdout


def test_collection_and_synthetic_metadata_ignore_ambient_private_paths(tmp_path):
    sentinel = tmp_path / "fictional-private-sentinel"
    sentinel.mkdir()
    (sentinel / "metadata.csv").write_text("deliberately invalid fictional metadata\n")
    env = dict(os.environ)
    for key in ("FIN_PROJECT_ROOT", "FIN_DATA_DIR", "FIN_CACHE_DIR", "FIN_EXPORT_DIR"):
        env[key] = str(sentinel)
    # Reject reads, including a hardcoded repository data directory. The target
    # regression must pass without even consulting either location's metadata.
    script = f"""
import os, runpy, sys
from pathlib import Path
forbidden = [Path({str(sentinel)!r}).resolve(), Path({str(ROOT / 'data')!r}).resolve()]
def audit(event, args):
    if event == 'open' and args[1] is not None and isinstance(args[0], (str, bytes, os.PathLike)):
        path = Path(os.fsdecode(args[0])).resolve()
        if any(path == root or root in path.parents for root in forbidden):
            raise AssertionError('test read forbidden fictional sentinel directory')
sys.addaudithook(audit)
namespace = runpy.run_path({str(ROOT / 'tests/conftest.py')!r})
from src import config
for key, attribute in [('FIN_PROJECT_ROOT', 'PROJECT_ROOT'), ('FIN_DATA_DIR', 'DATA_DIR'),
                       ('FIN_CACHE_DIR', 'CACHE_DIR'), ('FIN_EXPORT_DIR', 'EXPORT_DIR')]:
    path = getattr(config, attribute)
    assert path == Path(os.environ[key]).resolve()
    assert path != forbidden[0]
    assert path == namespace['_GUARD_ROOT'].resolve() or namespace['_GUARD_ROOT'].resolve() in path.parents
import pytest
raise SystemExit(pytest.main([{str(ROOT / 'tests/test_sfcu_parser.py')!r}, '-q',
    '-k', 'synthetic_metadata_supplies_account_type_and_apr', '-p', 'no:cacheprovider']))
"""
    result = _run([sys.executable, "-c", script], env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    assert (sentinel / "metadata.csv").read_text() == "deliberately invalid fictional metadata\n"
    assert sorted(path.name for path in sentinel.iterdir()) == ["metadata.csv"]


def test_explicit_price_fixture_covers_latest_close_refresh(stub_prices):
    from src import prices
    from copy import deepcopy
    marks = {"2024-01-01": 10.0}
    stub_prices.set("FAKEA", marks)
    cache = prices._load_prices()
    cache["FAKEA"] = dict(marks)
    before = deepcopy(cache)
    assert prices.fetch_latest_close_batch(["FAKEA"], verbose=False) == 0
    assert cache == before
    assert stub_prices.registered == {"FAKEA": marks}


def test_child_module_preserves_arguments_and_isolated_environment(tmp_path, isolated_workdir):
    marker = tmp_path / "attempts"
    module = tmp_path / "fictional_module_probe.py"
    module.write_text("""
import os, sys
from pathlib import Path
assert sys.argv[0].endswith('fictional_module_probe.py')
assert sys.argv[1:] == ['fictional-argument']
root = Path(os.environ['FIN_PROJECT_ROOT'])
for key, folder in [('FIN_DATA_DIR', 'data'), ('FIN_CACHE_DIR', 'cache'),
                    ('FIN_EXPORT_DIR', 'exports')]:
    assert Path(os.environ[key]) == root / folder
assert os.environ['PYTHONPATH'] == str(root)
""")
    environment = dict(os.environ, PYTHONPATH=str(tmp_path))
    result = _run(child_command([sys.executable, "-m", module.stem,
                                "fictional-argument"], marker), env=environment)
    assert result.returncode == 0, result.stderr
    assert not marker.exists()
