"""Hooks use the environment created by uv even without shell activation."""
from pathlib import Path
import os
import json
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
                 ('config', 'user.email', 'developer@example.test'),
                 ('config', 'core.autocrlf', 'false')]:
        subprocess.run([git, '-C', str(repo), *args], check=True, capture_output=True)
    for relative in ['tools/privacy_guard.py', 'tools/run-privacy-guard.sh',
                     'tools/install-hooks.sh', 'githooks/pre-commit', 'githooks/pre-push']:
        target = repo / relative
        target.parent.mkdir(exist_ok=True)
        target.write_bytes((ROOT / relative).read_text(encoding='utf-8').encode('utf-8'))
    (repo / 'README.md').write_text('Entirely fictional test repository.\n')
    (repo / '.gitignore').write_text('.pii-denylist.txt\n.privacy-receipts.json\n.venv/\n')
    subprocess.run([git, '-C', str(repo), 'add', 'README.md', '.gitignore'],
                   check=True, capture_output=True)
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    if os.name == 'nt':
        # Native Python needs git.exe on PATH; copying it breaks Git's install
        # lookup. Git's own directories supply git/sh without exposing Python.
        search_path = os.pathsep.join([str(binaries), str(Path(git).parent), str(Path(shell).parent)])
    else:
        (binaries / 'git').symlink_to(git)
        (binaries / 'sh').symlink_to(shell)
        search_path = str(binaries)
    marker = tmp_path / 'unsupported-runtime-called'
    env = {**os.environ, 'PATH': search_path, 'FIN_TEST_RUNTIME_MARKER': str(marker),
           'GIT_CONFIG_NOSYSTEM': '1'}
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)

    def install_runtime(path, supported):
        path.parent.mkdir(exist_ok=True, parents=True)
        script = ('#!/bin/sh\nexec ' + shlex.quote(Path(sys.executable).as_posix()) + ' "$@"\n' if supported
                  else '#!/bin/sh\nprintf "called\\n" > "$FIN_TEST_RUNTIME_MARKER"\nexit 1\n')
        path.write_bytes(script.encode('utf-8'))
        path.chmod(0o755)

    def run(*args, input=None):
        return subprocess.run([shell, *args], cwd=repo, env=env,
                              capture_output=True, text=True, input=input, timeout=30)

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


@pytest.mark.parametrize('path,market', [
    ('cache/dividends_cache.json', {'FICT': [['2024-01-02', 0.5]]}),
    ('cache/sector_cache.json', {'FICT': 'Other'}),
    ('cache/splits_cache.json', {'FICT': [['2024-01-02', 2.0]]}),
    ('cache/symbol_proxy_map.json', {'Fictional Fund': {'proxy': 'FICT', 'method': 'scaled'}}),
])
@pytest.mark.parametrize('empty', [True, False], ids=['empty', 'market-shaped'])
def test_precommit_blocks_force_added_private_runtime_cache(hook_repo, path, market, empty):
    repo, binaries, _, install_runtime, run = hook_repo
    install_runtime(binaries / 'python3', supported=True)
    assert run('tools/install-hooks.sh').returncode == 0
    with (repo / '.gitignore').open('a') as stream:
        stream.write(path + '\n')
    target = repo / path
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps({} if empty else market))
    subprocess.run(['git', '-C', str(repo), 'add', '-f', path], check=True, capture_output=True)
    result = run('githooks/pre-commit')
    assert result.returncode == 1
    assert 'private-file: <private-path>' in result.stderr
    assert path not in result.stderr
    assert 'FICT' not in result.stderr
    assert 'PASS' not in result.stdout


@pytest.mark.parametrize('path', [
    'cache/dividends_cache.json', 'cache/sector_cache.json',
    'cache/splits_cache.json', 'cache/symbol_proxy_map.json',
])
def test_prepush_rejects_intermediate_cache_even_after_removal(hook_repo, path):
    repo, binaries, _, install_runtime, run = hook_repo
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.DEVNULL).decode().strip()
    # Construct the hypothetical outgoing history before installing hooks in
    # this disposable repository. Never disable a real repository's hooks.
    git('commit', '-m', 'Initial fictional hook fixture')
    base = git('rev-parse', 'HEAD')
    target = repo / path
    target.parent.mkdir(exist_ok=True)
    target.write_text('{}')
    git('add', '-f', path)
    git('commit', '-m', 'Synthetic outgoing cache fixture')
    intermediate = git('rev-parse', 'HEAD')
    git('rm', path)
    git('commit', '-m', 'Remove fictional cache fixture')
    head = git('rev-parse', 'HEAD')
    install_runtime(binaries / 'python3', supported=True)
    assert run('tools/install-hooks.sh').returncode == 0
    # A known nonzero base requires no destination advertisement/network lookup.
    result = run('githooks/pre-push', 'example', 'unused-local-destination',
                 input=f'local {head} remote {base}\n')
    assert result.returncode == 1
    assert 'private-file: <private-path>' in result.stderr
    assert intermediate[:12] in result.stderr
    assert path not in result.stderr
    assert 'PASS' not in result.stdout
