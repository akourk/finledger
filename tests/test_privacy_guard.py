"""Privacy boundaries use invented tokens in disposable repositories."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys

import pytest

from tools import privacy_guard as G


@pytest.fixture
def repo(tmp_path):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(tmp_path), *args], stderr=subprocess.DEVNULL).decode().strip()
    git('init')
    git('config', 'user.name', 'Example Developer')
    git('config', 'user.email', 'developer@example.test')
    (tmp_path / '.gitignore').write_text('.pii-denylist.txt\n.privacy-receipts.json\ndata/\n')
    (tmp_path / 'README.md').write_text('Synthetic test repository\n')
    git('add', '.')
    git('commit', '-m', 'Initial synthetic content')
    return tmp_path, git


def token():
    return 'private-' + 'review-token-12345'


def test_staged_reads_index_not_worktree(repo):
    root, git = repo
    path = root / 'note.md'
    path.write_text(token())
    git('add', 'note.md')
    path.write_text('Clean working copy does not excuse staged contents.')
    assert any(f.rule == 'local-denylist' for f in G.scan_scope(root, 'staged', [token()]))
    assert not G.scan_scope(root, 'worktree', [token()])


def test_complete_index_not_only_added_lines(repo):
    root, git = repo
    (root / 'note.md').write_text(token())
    git('add', 'note.md')
    git('commit', '-m', 'Synthetic preexisting token')
    (root / 'other.md').write_text('Unrelated new content')
    git('add', 'other.md')
    assert any(f.rule == 'local-denylist' for f in G.scan_scope(root, 'staged', [token()]))


def test_force_added_private_file_cannot_hide_behind_gitignore(repo):
    root, git = repo
    (root / 'data').mkdir()
    (root / 'data' / 'statement.csv').write_text('fictional fixture')
    git('add', '-f', 'data/statement.csv')
    findings = G.scan_scope(root, 'staged', [])
    assert any(f.rule == 'private-file' for f in findings)
    assert all('statement' not in f.path for f in findings)


def test_intermediate_outgoing_commit_is_scanned(repo):
    root, git = repo
    base = git('rev-parse', 'HEAD')
    (root / 'note.md').write_text(token())
    git('add', 'note.md')
    git('commit', '-m', 'Temporary synthetic content')
    bad = git('rev-parse', 'HEAD')
    (root / 'note.md').write_text('Clean final content')
    git('add', 'note.md')
    git('commit', '-m', 'Remove synthetic token')
    head = git('rev-parse', 'HEAD')
    commits = G.outgoing(root, f'refs/heads/main {head} refs/heads/main {base}\n')
    found = G.scan_commits(root, commits, [token()])
    assert any(f.rule == 'local-denylist' and f.revision == bad[:12] for f in found)


def test_outgoing_commit_messages_are_scanned(repo):
    root, git = repo
    base = git('rev-parse', 'HEAD')
    git('commit', '--allow-empty', '-m', token())
    commits = G.outgoing(root, f'local {git("rev-parse", "HEAD")} remote {base}\n')
    assert any(f.path == '<commit-message>' for f in G.scan_commits(root, commits, [token()]))


def test_new_remote_branch_scans_entire_ancestry(repo):
    root, git = repo
    head = git('rev-parse', 'HEAD')
    assert head in G.outgoing(root, f'local {head} remote {"0" * 40}\n')
    assert G.outgoing(root, f'local {"0" * 40} remote {head}\n') == []


def test_local_guard_requires_initialization(repo):
    root, _ = repo
    with pytest.raises(ValueError, match='not initialized'):
        G.load_tokens(root, True)
    G.initialize(root)
    assert (root / '.pii-denylist.txt').exists()
    (root / '.pii-denylist.txt').write_text(token())
    G.initialize(root)
    assert G.load_tokens(root, True) == [token()]


def test_diagnostics_do_not_reveal_tokens_or_lines(capsys):
    raw = 'account-' + '765432109'
    findings = G.text_findings('note-' + raw + '.md', f'private {raw}', [raw])
    G.print_findings(findings, [raw])
    output = capsys.readouterr().err
    assert 'BLOCKED' in output
    assert raw not in output
    assert 'private ' not in output


@pytest.mark.parametrize('value', ['$12,345.67', '12345.67', 'balance=12345.6700'])
def test_numeric_denylist_matches_format_variants(value):
    assert G.denied(value, '12,345.67')
    assert not G.denied('912345.67', '12,345.67')


def test_market_data_has_no_blanket_denylist_exemption():
    payload = json.dumps({'2026-01-02': 12345.67}).encode()
    assert any(f.rule == 'local-denylist' for f in G.inspect_file('cache/prices/TEST.json', payload, '100644', ['12345.67']))
    malformed = json.dumps({'salary': 100}).encode()
    assert any(f.rule == 'non-market-price-cache' for f in G.inspect_file('cache/prices/TEST.json', malformed, '100644', []))


def test_personal_cache_anchors_rejected():
    payload = json.dumps({'TEST': {'anchor_' + 'price': 42}}).encode()
    assert any(f.rule == 'personal-cache-anchor' for f in G.inspect_file('cache/symbol_proxy_map.json', payload, '100644', []))


def test_generic_identifiers_and_secret_patterns():
    examples = ['person@' + 'private-mail.invalid', 'gh' + 'p_' + 'a' * 36,
                '/Users/' + 'privateperson/file', '123' + '-45-' + '6789']
    assert all(G.text_findings('note.md', value, []) for value in examples)
    assert not G.text_findings('note.md', 'person@example.test', [])


def test_missing_or_modified_artifact_provenance_is_rejected(repo):
    root, _ = repo
    site = root / 'site'
    site.mkdir()
    (site / 'index.html').write_text('<html>fictional claim without provenance</html>')
    assert G.scan_artifact(root, site, [])
    source = root / 'tools' / 'build_sample_snapshot.py'
    source.parent.mkdir()
    source.write_text('# synthetic test generator\n')
    demo = {'synthetic': True, 'as_of': '2026-06-30', 'source': 'tools/build_sample_snapshot.py',
            'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'snapshot_sha256': 'a' * 64}
    (site / 'index.html').write_text('<script>const DATA = ' + json.dumps({'demo': demo}) + ';</script>')
    manifest = dict(demo, schema=1, files={'index.html': hashlib.sha256((site / 'index.html').read_bytes()).hexdigest()})
    (site / 'provenance.json').write_text(json.dumps(manifest))
    # Self-authored flags and an arbitrary snapshot hash do not prove origin.
    assert G.scan_artifact(root, site, [])
    (site / 'index.html').write_text('tampered')
    assert G.scan_artifact(root, site, [])


def test_failed_git_inspection_is_a_failure(repo):
    root, _ = repo
    with pytest.raises(ValueError, match='refusing'):
        G.git(root, 'rev-list', 'no-such-revision')


def test_symlinks_are_not_followed(repo):
    root, git = repo
    target = root.parent / 'private-example.txt'
    target.write_text(token())
    (root / 'link').symlink_to(target)
    git('add', 'link')
    assert any(f.rule == 'uninspectable-link-or-submodule' for f in G.scan_scope(root, 'staged', []))


def test_identifiers_in_filenames_are_not_printed(capsys):
    private_name = 'person@' + 'private-mail.invalid.md'
    found = G.inspect_file(private_name, b'otherwise harmless', '100644', [])
    assert any(f.path == '<filename>' for f in found)
    G.print_findings(found, [])
    assert private_name not in capsys.readouterr().err


def test_hook_rejects_staged_file_and_commit_message(repo):
    import shutil
    root, git = repo
    original = Path(__file__).resolve().parents[1]
    (root / 'tools').mkdir()
    (root / 'githooks').mkdir()
    shutil.copy2(original / 'tools/privacy_guard.py', root / 'tools/privacy_guard.py')
    shutil.copy2(original / 'tools/run-privacy-guard.sh', root / 'tools/run-privacy-guard.sh')
    runtime = root / '.venv/bin/python'
    runtime.parent.mkdir(parents=True)
    runtime.symlink_to(sys.executable)
    for name in ('pre-commit', 'commit-msg', 'pre-push'):
        shutil.copy2(original / 'githooks' / name, root / 'githooks' / name)
    git('add', 'tools', 'githooks')
    git('commit', '-m', 'Install fictional test infrastructure')
    git('config', 'core.hooksPath', 'githooks')
    (root / '.pii-denylist.txt').write_text(token())
    (root / 'note.md').write_text(token())
    git('add', 'note.md')
    result = subprocess.run(['git', '-C', str(root), 'commit', '-m', 'Safe message'], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'BLOCKED local-denylist' in result.stderr
    assert token() not in result.stderr
    (root / 'note.md').write_text('Fictional harmless input')
    git('add', 'note.md')
    result = subprocess.run(['git', '-C', str(root), 'commit', '-m', token()], capture_output=True, text=True)
    assert result.returncode != 0
    assert '<commit-message>' in result.stderr
    assert token() not in result.stderr


def _sample_source(root, git):
    source = root / G.SAMPLE_SOURCE
    source.parent.mkdir(exist_ok=True)
    source.write_text("def build_snapshot(path):\n    path.write_text('{\"version\":1,\"files\":{}}\\n')\n")
    sample = root / G.SAMPLE_PATH
    sample.parent.mkdir(exist_ok=True)
    sample.write_text('{"version":1,"files":{}}\n')
    git('add', G.SAMPLE_SOURCE, G.SAMPLE_PATH)
    return source, sample


def test_sample_is_generated_independently_and_checked_against_index(repo):
    root, git = repo
    source, sample = _sample_source(root, git)
    assert not G.scan_scope(root, 'staged', [])
    sample.write_text('{"version":1,"files":{"statement.csv":"fictional tampering"}}\n')
    assert any(f.rule == 'unverified-synthetic-snapshot' for f in G.scan_scope(root, 'worktree', []))
    assert not G.scan_scope(root, 'staged', [])
    git('add', G.SAMPLE_PATH)
    assert any(f.rule == 'unverified-synthetic-snapshot' for f in G.scan_scope(root, 'staged', []))


def test_unstaged_generator_cannot_approve_staged_snapshot(repo):
    root, git = repo
    source, sample = _sample_source(root, git)
    source.write_text(source.read_text() + '# Unstaged generator change\n')
    assert any(f.rule == 'unverified-synthetic-snapshot' for f in G.scan_scope(root, 'staged', []))
    git('add', G.SAMPLE_SOURCE)
    assert not G.scan_scope(root, 'staged', [])


def test_old_snapshot_is_never_verified_by_executing_historical_code(repo):
    root, git = repo
    source, sample = _sample_source(root, git)
    git('commit', '-m', 'Old synthetic sample')
    old = git('rev-parse', 'HEAD')
    source.write_text("def build_snapshot(path):\n    path.write_text('{\"version\":1,\"files\":{},\"generation\":2}\\n')\n")
    assert any(f.rule == 'unverified-synthetic-snapshot' for f in G.scan_commits(root, [old], []))


def test_mode_change_reusing_blob_is_still_scanned(repo):
    root, git = repo
    file = root / 'link'
    file.write_text('relative-target')
    git('add', 'link'); git('commit', '-m', 'Ordinary synthetic file')
    first = git('rev-parse', 'HEAD')
    file.unlink(); file.symlink_to('relative-target')
    git('add', 'link'); git('commit', '-m', 'Synthetic symlink')
    second = git('rev-parse', 'HEAD')
    found = G.scan_commits(root, [first, second], [])
    assert any(f.rule == 'uninspectable-link-or-submodule' and f.revision == second[:12] for f in found)


def test_annotated_tags_are_scanned_even_when_commit_is_already_published(repo):
    root, git = repo
    commit = git('rev-parse', 'HEAD')
    git('tag', '-a', 'example-tag', '-m', token())
    tag = git('rev-parse', 'example-tag')
    found = G.scan_push(root, f'refs/tags/example-tag {tag} refs/tags/example-tag {commit}\n', [token()])
    assert any(f.path == '<tag-annotation>' and f.rule == 'local-denylist' for f in found)


def test_exact_local_and_remote_ref_names_are_scanned_without_echo(repo, capsys):
    root, git = repo
    commit = git('rev-parse', 'HEAD')
    for local, remote in [('refs/heads/' + token(), 'refs/heads/example'),
                          ('refs/heads/example', 'refs/heads/' + token())]:
        found = G.scan_push(root, f'{local} {commit} {remote} {commit}\n', [token()])
        assert any(f.path == '<ref-name>' for f in found)
        G.print_findings(found, [token()])
        assert token() not in capsys.readouterr().err


def test_new_branch_uses_exact_destination_advertisement(repo, tmp_path):
    root, git = repo
    remote = root / 'destination.git'
    subprocess.check_call(['git', 'init', '--bare', str(remote)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # This already-published old snapshot cannot be regenerated by current code.
    (root / 'samples').mkdir()
    (root / G.SAMPLE_PATH).write_text('{"version":1,"files":{"old.csv":"old fictional baseline"}}')
    git('add', G.SAMPLE_PATH); git('commit', '-m', 'Already published synthetic baseline')
    base = git('rev-parse', 'HEAD')
    git('push', str(remote), 'HEAD:refs/heads/main')
    (root / G.SAMPLE_PATH).unlink()
    git('add', '-u'); git('commit', '-m', 'Remove obsolete synthetic sample')
    head = git('rev-parse', 'HEAD')
    description = f'refs/heads/topic {head} refs/heads/topic {"0" * 40}\n'
    assert G.outgoing(root, description, str(remote)) == [head]
    assert not G.scan_push(root, description, [], str(remote))
    # An unrelated destination must not inherit another remote's trust.
    other = root / 'other.git'
    subprocess.check_call(['git', 'init', '--bare', str(other)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert base in G.outgoing(root, description, str(other))
    assert any(f.rule == 'unverified-synthetic-snapshot' for f in G.scan_push(root, description, [], str(other)))


def test_failed_destination_advertisement_is_not_a_pass(repo):
    root, git = repo
    head = git('rev-parse', 'HEAD')
    with pytest.raises(ValueError, match='refusing'):
        G.outgoing(root, f'local {head} remote {"0" * 40}\n', str(root / 'missing.git'))


def test_reviewed_screenshot_hashes_are_required(repo, monkeypatch):
    root, _ = repo
    source = b'# Synthetic screenshot generator\n'
    image = b'\x89PNG\x00fictional-image'
    html = b'<html>verified fictional artifact</html>'
    files = {G.SAMPLE_SOURCE: ('100644', source),
             'docs/img/example.png': ('100644', image)}
    monkeypatch.setattr(G, 'build_sources', lambda root, kind: {G.SAMPLE_SOURCE: source})
    monkeypatch.setattr(G, 'canonical_outputs', lambda root, kind, cache: {'index.html': html})
    assert G.verify_public_assets(root, files, {})
    registry = {'schema': 1, 'visually_reviewed': True,
                'source_sha256': hashlib.sha256(source).hexdigest(),
                'artifact_sha256': hashlib.sha256(html).hexdigest(),
                'files': {'example.png': hashlib.sha256(image).hexdigest()}}
    files[G.IMAGE_REGISTRY] = ('100644', json.dumps(registry).encode())
    assert not G.verify_public_assets(root, files, {})
    files['docs/img/example.png'] = ('100644', image + b'tampered')
    assert G.verify_public_assets(root, files, {})


def test_canonical_build_failure_never_echoes_subprocess_logs(repo, capsys):
    root, git = repo
    source, _ = _sample_source(root, git)
    source.write_text("raise RuntimeError('" + token() + "')\n")
    git('add', G.SAMPLE_SOURCE)
    found = G.scan_scope(root, 'staged', [])
    assert any(f.rule == 'unverified-synthetic-snapshot' for f in found)
    G.print_findings(found, [])
    assert token() not in capsys.readouterr().err


def test_artifact_must_equal_independent_build_even_after_rehashing(repo, monkeypatch):
    root, _ = repo
    source = root / G.SAMPLE_SOURCE
    source.parent.mkdir()
    source.write_text('# Reviewed fictional generator\n')
    site = root / 'site'
    site.mkdir()
    demo = {'synthetic': True, 'as_of': '2026-06-30', 'source': G.SAMPLE_SOURCE,
            'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'snapshot_sha256': 'b' * 64}
    html = '<html><script>const DATA = ' + json.dumps({'demo': demo, 'transactions': []}) + ';</script></html>'
    manifest = dict(demo, schema=1, files={'index.html': hashlib.sha256(html.encode()).hexdigest()})
    expected = {'index.html': html.encode(), 'provenance.json': json.dumps(manifest).encode()}
    for name, data in expected.items():
        (site / name).write_bytes(data)
    builds = []
    def independent(root, kind, cache):
        builds.append(kind)
        return expected
    monkeypatch.setattr(G, 'canonical_outputs', independent)
    assert not G.scan_artifact(root, site, [])
    assert builds == ['demo']
    altered = html.replace('"transactions": []', '"transactions": [{"amount": 700}]')
    (site / 'index.html').write_text(altered)
    manifest['files']['index.html'] = hashlib.sha256(altered.encode()).hexdigest()
    (site / 'provenance.json').write_text(json.dumps(manifest))
    assert any(f.rule == 'unverified-demo-provenance' for f in G.scan_artifact(root, site, []))


def test_sample_subprocess_has_isolated_paths_and_no_ambient_imports(repo, monkeypatch):
    root, git = repo
    source, _ = _sample_source(root, git)
    source.write_text(
        "import os\nfrom pathlib import Path\n"
        "def build_snapshot(path):\n"
        "    assert os.environ['FIN_DATA_DIR'] != 'ambient-private-data'\n"
        "    assert not (Path(__file__).parents[1] / 'private-marker').exists()\n"
        "    assert 'PYTHONPATH' not in os.environ\n"
        "    path.write_text('synthetic verified output')\n")
    (root / 'private-marker').write_text('Fictional sentinel, never copied to build')
    monkeypatch.setenv('FIN_DATA_DIR', 'ambient-private-data')
    monkeypatch.setenv('PYTHONPATH', str(root))
    assert G.canonical_outputs(root, 'sample', {})[G.SAMPLE_PATH] == b'synthetic verified output'


def test_commit_and_prospective_identities_are_checked_without_echo(repo, capsys):
    root, git = repo
    private_email = 'synthetic@' + 'private-mail.invalid'
    git('config', 'user.email', private_email)
    prospective = G.prospective_identities(root, [])
    assert any(f.rule == 'non-example-email' for f in prospective)
    git('commit', '--allow-empty', '-m', 'Safe synthetic message')
    found = G.scan_commits(root, [git('rev-parse', 'HEAD')], [])
    assert any(f.path == '<commit-identity>' for f in found)
    G.print_findings(found + prospective, [])
    assert private_email not in capsys.readouterr().err


def test_environment_author_override_cannot_bypass_identity_check(repo, monkeypatch):
    root, _ = repo
    monkeypatch.setenv('GIT_AUTHOR_EMAIL', 'synthetic@' + 'private-mail.invalid')
    assert G.prospective_identities(root, [])
    monkeypatch.setenv('GIT_AUTHOR_EMAIL', 'example@users.noreply.github.com')
    assert not G.prospective_identities(root, [])


def test_annotated_tag_tagger_identity_is_checked(repo, capsys):
    root, git = repo
    commit = git('rev-parse', 'HEAD')
    private_email = 'synthetic@' + 'private-mail.invalid'
    git('config', 'user.email', private_email)
    git('tag', '-a', 'identity-example', '-m', 'Harmless synthetic annotation')
    local = git('rev-parse', 'identity-example')
    found = G.scan_push(root, f'local {local} remote {commit}\n', [])
    assert any(f.path == '<tagger-identity>' for f in found)
    G.print_findings(found, [])
    assert private_email not in capsys.readouterr().err


def test_same_annotated_tag_can_be_pushed_under_two_safe_refs(repo):
    root, git = repo
    commit = git('rev-parse', 'HEAD')
    git('tag', '-a', 'first-example', '-m', 'Synthetic annotation')
    tag = git('rev-parse', 'first-example')
    refs = (f'refs/tags/first-example {tag} refs/tags/first-example {commit}\n'
            f'refs/tags/second-example {tag} refs/tags/second-example {commit}\n')
    assert not G.scan_push(root, refs, [])


def _reviewable_screenshots(root, git):
    _sample_source(root, git)
    (root / 'samples/prices.fixture.json').write_text('{}')
    (root / 'tools/demo_banner.py').write_text('# Fictional demo banner\n')
    (root / 'tools/build_demo.py').write_text(
        "import sys\nfrom pathlib import Path\n"
        "def main():\n"
        "    root = Path(__file__).resolve().parents[1]\n"
        "    out = Path(sys.argv[2]); out.mkdir()\n"
        "    (out/'index.html').write_text('<html>'+(root/'src/ui.js').read_text()+'</html>')\n"
        "    (out/'provenance.json').write_text('{}')\n")
    (root / 'src').mkdir()
    (root / 'src/ui.js').write_text('Synthetic A')
    images = root / 'docs/img'
    images.mkdir(parents=True)
    image = b'\x89PNG\x00synthetic pixels'
    (images / 'example.png').write_bytes(image)
    def reviewed():
        html = G.canonical_outputs(root, 'demo', {})['index.html']
        registry = {'schema': 1, 'visually_reviewed': True,
                    'source_sha256': hashlib.sha256((root / G.SAMPLE_SOURCE).read_bytes()).hexdigest(),
                    'artifact_sha256': hashlib.sha256(html).hexdigest(),
                    'files': {'example.png': hashlib.sha256(image).hexdigest()}}
        (root / G.IMAGE_REGISTRY).write_text(json.dumps(registry))
    reviewed()
    git('add', '.')
    return reviewed


def test_verified_screenshot_series_remains_pushable_without_running_history(repo):
    root, git = repo
    reviewed = _reviewable_screenshots(root, git)
    assert not G.scan_scope(root, 'staged', [])
    git('commit', '-m', 'Verified fictional screenshots A')
    first = git('rev-parse', 'HEAD')
    (root / 'src/ui.js').write_text('Synthetic B')
    git('add', 'src/ui.js')
    assert any(f.rule == 'unverified-screenshot-review' for f in G.scan_scope(root, 'staged', []))
    reviewed(); git('add', G.IMAGE_REGISTRY)
    assert not G.scan_scope(root, 'staged', [])
    git('commit', '-m', 'Verified fictional screenshots B')
    second = git('rev-parse', 'HEAD')
    assert not G.scan_commits(root, [first, second], [])
    receipt = root / G.RECEIPT_PATH
    contents = json.loads(receipt.read_text())
    assert len(contents['verified_assets']) == 2
    assert all(len(value) == 64 for value in contents['verified_assets'])
    receipt.unlink()
    assert any(f.rule == 'unverified-screenshot-review' and f.revision == first[:12]
               for f in G.scan_commits(root, [first, second], []))


def test_blocked_complete_scan_cannot_issue_asset_receipt(repo):
    root, git = repo
    _sample_source(root, git)
    (root / 'note.md').write_text(token())
    git('add', 'note.md')
    assert G.scan_scope(root, 'staged', [token()])
    assert not (root / G.RECEIPT_PATH).exists()


def test_receipts_are_private_and_do_not_override_other_content_rules(repo):
    root, git = repo
    _sample_source(root, git)
    assert not G.scan_scope(root, 'staged', [])
    git('commit', '-m', 'Verified synthetic sample')
    (root / 'note.md').write_text(token())
    git('add', 'note.md'); git('commit', '-m', 'Synthetic denied content')
    found = G.scan_commits(root, [git('rev-parse', 'HEAD')], [token()])
    assert any(f.rule == 'local-denylist' for f in found)
    git('add', '-f', G.RECEIPT_PATH)
    assert any(f.rule == 'private-file' for f in G.scan_scope(root, 'staged', []))
