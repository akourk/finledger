"""Hooks use the environment created by uv even without shell activation."""
from pathlib import Path
import os
import shlex
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def hook_repo(tmp_path):
    repo = tmp_path / 'repository'
    repo.mkdir()
    git = shutil.which('git')
    shell = shutil.which('sh')
    assert git and shell
    for args in [('init', '-q'), ('config', 'user.name', 'Example Developer'),
                 ('config', 'user.email', 'developer@example.test')]:
        subprocess.run([git, '-C', str(repo), *args], check=True, capture_output=True)
    for relative in ['tools/privacy_guard.py', 'tools/run-privacy-guard.sh',
                     'tools/install-hooks.sh', 'githooks/pre-commit']:
        target = repo / relative
        target.parent.mkdir(exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    (repo / 'README.md').write_text('Entirely fictional test repository.\n')
    (repo / '.gitignore').write_text('.pii-denylist.txt\n.privacy-receipts.json\n.venv/\n')
    subprocess.run([git, '-C', str(repo), 'add', 'README.md', '.gitignore'],
                   check=True, capture_output=True)
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    (binaries / 'git').symlink_to(git)
    (binaries / 'sh').symlink_to(shell)
    marker = tmp_path / 'unsupported-runtime-called'
    env = {**os.environ, 'PATH': str(binaries), 'FIN_TEST_RUNTIME_MARKER': str(marker),
           'GIT_CONFIG_NOSYSTEM': '1'}
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)

    def install_runtime(path, supported):
        path.parent.mkdir(exist_ok=True, parents=True)
        script = ('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' "$@"\n' if supported
                  else '#!/bin/sh\nprintf "called\\n" > "$FIN_TEST_RUNTIME_MARKER"\nexit 1\n')
        path.write_text(script)
        path.chmod(0o755)

    def run(*args):
        return subprocess.run([shell, *args], cwd=repo, env=env,
                              capture_output=True, text=True, timeout=30)

    return repo, binaries, marker, install_runtime, run


def test_installer_and_precommit_prefer_unactivated_project_python(hook_repo):
    repo, binaries, marker, install_runtime, run = hook_repo
    install_runtime(binaries / 'python3', supported=False)
    install_runtime(binaries / 'python', supported=False)
    install_runtime(repo / '.venv/bin/python', supported=True)
    installed = run('tools/install-hooks.sh')
    assert installed.returncode == 0, installed.stderr
    assert (repo / '.pii-denylist.txt').is_file()
    checked = run('githooks/pre-commit')
    assert checked.returncode == 0, checked.stderr
    assert 'PASS' in checked.stdout
    assert not marker.exists(), 'system Python must not take precedence over the uv environment'


def test_runner_can_use_supported_system_python_without_a_venv(hook_repo):
    repo, binaries, _, install_runtime, run = hook_repo
    install_runtime(binaries / 'python3', supported=True)
    result = run('tools/run-privacy-guard.sh', 'scan', '--scope', 'staged')
    assert result.returncode == 0, result.stderr
    assert 'PASS' in result.stdout
    assert not (repo / '.venv').exists()


def test_runner_fails_closed_when_only_unsupported_interpreters_exist(hook_repo):
    _, binaries, marker, install_runtime, run = hook_repo
    install_runtime(binaries / 'python3', supported=False)
    install_runtime(binaries / 'python', supported=False)
    result = run('tools/run-privacy-guard.sh', 'scan', '--scope', 'staged')
    assert result.returncode == 2
    assert 'Python 3.10+' in result.stderr
    assert 'uv sync --locked --all-groups' in result.stderr
    assert 'PASS' not in result.stdout
    assert marker.exists()
