"""Scan public content without printing private values.

The staged scan reads the index, not working files. The push hook checks every
outgoing commit, including intermediate versions and messages. Local tokens stay
in the ignored denylist; CI uses structural and generic checks independently.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass

SAMPLE_PATH = "samples/portfolio.snapshot.json"
SAMPLE_SOURCE = "tools/build_sample_snapshot.py"
IMAGE_REGISTRY = "docs/img/provenance.json"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
RECEIPT_PATH = ".privacy-receipts.json"


def asset_fingerprint(files: dict[str, tuple[str, bytes]]) -> str | None:
    """Bind receipts to every approved asset and its actual build inputs."""
    images = {name for name in files if name.startswith("docs/img/")
              and Path(name).suffix.lower() in IMAGE_SUFFIXES}
    names = {SAMPLE_PATH, SAMPLE_SOURCE} if SAMPLE_PATH in files else set()
    if images:
        names.update(images)
        names.update({IMAGE_REGISTRY, SAMPLE_SOURCE, SAMPLE_PATH,
                      "samples/prices.fixture.json", "tools/build_demo.py", "tools/demo_banner.py"})
        names.update(name for name in files if name.startswith("src/")
                     and Path(name).suffix in {".py", ".js", ".css", ".html"})
    if not names or any(name not in files for name in names):
        return None
    record = [(name, files[name][0], hashlib.sha256(files[name][1]).hexdigest())
              for name in sorted(names)]
    return hashlib.sha256(json.dumps(record, separators=(",", ":")).encode()).hexdigest()


def verified_receipts(root: Path) -> set[str]:
    path = root / RECEIPT_PATH
    if not path.exists():
        return set()
    if path.is_symlink():
        raise ValueError("local verification receipt store must not be a link")
    try:
        record = json.loads(path.read_text())
        hashes = record["verified_assets"]
        if (record.get("schema") != 1 or not isinstance(hashes, list)
                or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                       for value in hashes)):
            raise ValueError("invalid receipt")
        return set(hashes)
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("local verification receipt store is malformed; details withheld") from None


def record_verified_assets(root: Path, files: dict[str, tuple[str, bytes]]) -> None:
    """Issue receipts only after a complete clean current-content scan."""
    fingerprint = asset_fingerprint(files)
    if fingerprint is None:
        return
    ignored = subprocess.run(["git", "-C", str(root), "check-ignore", "--quiet", RECEIPT_PATH],
                             capture_output=True)
    if ignored.returncode:
        raise ValueError("verification receipts must be gitignored before scanning public assets")
    receipts = verified_receipts(root)
    if fingerprint in receipts:
        return
    receipts.add(fingerprint)
    data = json.dumps({"schema": 1, "verified_assets": sorted(receipts)}, indent=2) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=".fin-privacy-receipts-", dir=root)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, root / RECEIPT_PATH)
    finally:
        Path(temporary).unlink(missing_ok=True)


def build_sources(root: Path, kind: str) -> dict[str, bytes]:
    """Only reviewed current source is executable; historical blobs are data."""
    names = {SAMPLE_SOURCE}
    if kind == "demo":
        names.update({"tools/build_demo.py", "tools/demo_banner.py", SAMPLE_PATH,
                      "samples/prices.fixture.json"})
        names.update(path.relative_to(root).as_posix() for path in (root / "src").rglob("*")
                     if path.suffix in {".py", ".js", ".css", ".html"})
    contents = {}
    for name in sorted(names):
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("trusted build sources are unavailable")
        contents[name] = path.read_bytes()
    return contents


def canonical_outputs(root: Path, kind: str, cache: dict) -> dict[str, bytes]:
    """Rebuild in a copied source tree with no portfolio data and private logs.

    The cache lasts one scan and is keyed by all source bytes. No historical
    code runs, and no build can read this checkout's data/cache by default.
    """
    sources = build_sources(root, kind)
    fingerprint = tuple((name, hashlib.sha256(data).hexdigest()) for name, data in sources.items())
    key = (kind, fingerprint)
    if key in cache:
        return cache[key]
    with tempfile.TemporaryDirectory(prefix="fin-privacy-build-") as directory:
        scratch = Path(directory)
        project = scratch / "project"
        for name, data in sources.items():
            path = project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("FIN_") and key not in {"PYTHONPATH", "PYTHONHOME"}}
        environment.update({"FIN_PROJECT_ROOT": str(scratch / "state"),
                            "FIN_DATA_DIR": str(scratch / "state/data"),
                            "FIN_CACHE_DIR": str(scratch / "state/cache"),
                            "FIN_EXPORT_DIR": str(scratch / "state/exports"),
                            "PYTHONHASHSEED": "0", "TZ": "UTC"})
        # -I excludes ambient module paths and cwd from imports. Only the
        # explicitly copied public source tree enters this process's sys.path.
        program = ("import sys; from pathlib import Path; "
                   "sys.path.insert(0, sys.argv[1]); ")
        if kind == "sample":
            program += "from tools.build_sample_snapshot import build_snapshot; build_snapshot(Path(sys.argv[2]))"
            output = scratch / "sample.json"
        else:
            program += "from tools.build_demo import main; sys.argv = ['build_demo', '--output', sys.argv[2]]; main()"
            output = scratch / "site"
        try:
            result = subprocess.run([sys.executable, "-I", "-c", program, str(project), str(output)],
                                    cwd=scratch, env=environment, capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            raise ValueError("independent synthetic build failed; details withheld") from None
        if result.returncode:
            raise ValueError("independent synthetic build failed; details withheld")
        if kind == "sample":
            expected = {SAMPLE_PATH: output.read_bytes()}
        else:
            expected = {path.name: path.read_bytes() for path in output.iterdir() if path.is_file()}
        cache[key] = expected
        return expected


def verify_public_assets(root: Path, files: dict[str, tuple[str, bytes]], cache: dict,
                         *, historical: bool = False) -> list[Finding]:
    """Bind sample/screenshot exceptions to independently verified provenance.

    Historical snapshots are accepted only if they equal today's independently
    generated canonical sample; older variants need deliberate local review.
    """
    out = []
    # An older, independently verified variant remains eligible without ever
    # executing its historical source. Other content rules still run separately.
    fingerprint = asset_fingerprint(files)
    if historical and fingerprint is not None:
        if "verified-receipts" not in cache:
            cache["verified-receipts"] = verified_receipts(root)
        if fingerprint in cache["verified-receipts"]:
            return []
    if SAMPLE_PATH in files:
        try:
            source = build_sources(root, "sample")
            source_matches = all(files.get(name) == ("100644", data) or files.get(name) == ("100755", data)
                                 for name, data in source.items())
            expected = canonical_outputs(root, "sample", cache)[SAMPLE_PATH]
            if files[SAMPLE_PATH][1] != expected or (not historical and not source_matches):
                raise ValueError("sample provenance mismatch")
        except (OSError, ValueError):
            out.append(Finding(SAMPLE_PATH, "unverified-synthetic-snapshot"))
    images = {name: value for name, value in files.items()
              if name.startswith("docs/img/") and Path(name).suffix.lower() in IMAGE_SUFFIXES}
    if images:
        try:
            registry = json.loads(files[IMAGE_REGISTRY][1])
            recorded = registry["files"]
            valid = (registry.get("schema") == 1 and registry.get("visually_reviewed") is True
                     and isinstance(recorded, dict)
                     and set(recorded) == {Path(name).name for name in images}
                     and all(recorded[Path(name).name] == hashlib.sha256(value[1]).hexdigest()
                             and value[0] in {"100644", "100755"} for name, value in images.items()))
            source = build_sources(root, "demo")
            if not historical:
                valid = valid and all(files.get(name) in (("100644", data), ("100755", data))
                                      for name, data in source.items())
            expected = canonical_outputs(root, "demo", cache)
            valid = (valid and registry.get("source_sha256") == hashlib.sha256(source[SAMPLE_SOURCE]).hexdigest()
                     and registry.get("artifact_sha256") == hashlib.sha256(expected["index.html"]).hexdigest())
            if not valid:
                raise ValueError("screenshot provenance mismatch")
        except (OSError, ValueError, KeyError, TypeError):
            out.append(Finding(IMAGE_REGISTRY, "unverified-screenshot-review"))
    return out

PRIVATE_PREFIXES = ("data/", "exports/", "audit/", "_site/", ".venv/", "node_modules/",
                    "cache/.fin-", "cache/prices/.fin-")
PRIVATE_NAMES = {".pii-denylist.txt", RECEIPT_PATH, "cache/last_run.json", "snap.json", "sample-dashboard.html"}
SAFE_MAIL_DOMAINS = {"example.com", "example.org", "example.net", "example.test", "test.invalid", "users.noreply.github.com"}
EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
RULES = (
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})\b")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b")),
    ("social-security-number", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")),
    ("personal-local-path", re.compile(r"(?:/Users/|/home/)[A-Za-z0-9_.-]+/|[A-Za-z]:\\Users\\[A-Za-z0-9_.-]+\\")),
    ("account-identifier", re.compile(r"\b(?:account[ _]*(?:number|id|no\.|#)|routing[ _]*(?:number|no\.|#)|ssn)\s*[:=]\s*[\"']?\d[\d -]{6,}", re.I)),
)


@dataclass(frozen=True)
class Finding:
    path: str
    rule: str
    revision: str = ""


def git(root: Path, *args: str, input: bytes | None = None) -> bytes:
    proc = subprocess.run(["git", "-C", str(root), *args], input=input,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode:
        # stderr can contain remote URLs, private paths or commit text.
        raise ValueError("git inspection failed; refusing to approve uninspected content")
    return proc.stdout


def repository(path: str | None = None) -> Path:
    base = Path(path or ".").resolve()
    return Path(git(base, "rev-parse", "--show-toplevel").decode().strip())


def load_tokens(root: Path, require_local: bool) -> list[str]:
    path = root / ".pii-denylist.txt"
    if not path.is_file():
        if require_local:
            raise ValueError("local privacy guard is not initialized; run sh tools/install-hooks.sh")
        return []
    return [line.strip() for line in path.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def denied(text: str, token: str) -> bool:
    numeric = token.replace(",", "").replace("$", "")
    if re.fullmatch(r"-?\d+(?:\.\d+)?", numeric):
        normalized = text.replace(",", "")
        return re.search(r"(?<![\d.])" + re.escape(numeric) + (r"0*" if "." in numeric else r"(?:\.0+)?") + r"(?![\d.])", normalized) is not None
    return token.casefold() in text.casefold()


def text_findings(path: str, text: str, tokens: list[str]) -> list[Finding]:
    found = [Finding(path, name) for name, pattern in RULES if pattern.search(text)]
    if any(m.group(1).lower() not in SAFE_MAIL_DOMAINS for m in EMAIL.finditer(text)):
        found.append(Finding(path, "non-example-email"))
    if any(denied(text, token) for token in tokens):
        found.append(Finding(path, "local-denylist"))
    return found


def protected(path: str) -> bool:
    p = PurePosixPath(path)
    if path == "data/.gitkeep":
        return False
    if path in PRIVATE_NAMES or path.startswith(PRIVATE_PREFIXES):
        return True
    if any(part == ".env" or part.startswith(".env.") for part in p.parts):
        return p.name not in {".env.example", ".env.sample"}
    if p.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".xlsx", ".xls", ".pdf", ".zip"}:
        return True
    if p.suffix.lower() in {".csv", ".tsv"}:
        return not path.startswith(("tests/fixtures/", "samples/"))
    return False


def finite(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def cache_findings(path: str, data: bytes) -> list[Finding]:
    if not path.startswith("cache/") or not path.endswith(".json"):
        return []
    try:
        obj = json.loads(data)
    except (ValueError, UnicodeError):
        return [Finding(path, "invalid-cache-json")]
    if path.startswith("cache/prices/"):
        prices = obj.get("prices") if isinstance(obj, dict) and "prices" in obj else obj
        wrapper_ok = not (isinstance(obj, dict) and "prices" in obj) or (
            set(obj) == {"prices", "symbol"} and obj["symbol"] == Path(path).stem)
        if not wrapper_ok or not isinstance(prices, dict) or any(
                not re.fullmatch(r"\d{4}-\d{2}-\d{2}", k) or not finite(v)
                for k, v in prices.items()):
            return [Finding(path, "non-market-price-cache")]
    if path == "cache/symbol_proxy_map.json":
        if not isinstance(obj, dict) or any(isinstance(v, dict) and
                                          any(k in v for k in ("anchor_date", "anchor_price"))
                                          for v in obj.values()):
            return [Finding(path, "personal-cache-anchor")]
    return []


def inspect_file(path: str, data: bytes, mode: str, tokens: list[str], *, artifact: bool = False) -> list[Finding]:
    out = text_findings("<filename>", path, tokens)
    if not artifact and protected(path):
        out.append(Finding("<private-path>", "private-file"))
    if mode in {"120000", "160000"}:
        out.append(Finding(path, "uninspectable-link-or-submodule"))
        return out
    # Approved public screenshots are reviewed visually; binary data elsewhere
    # is not silently skipped. A byte signature alone cannot prove provenance.
    if (b"\x00" in data and Path(path).suffix not in {".py", ".js", ".css", ".html", ".md"}) or data.startswith(b"\x89PNG"):
        allowed_image = path.startswith("docs/img/") and Path(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        if artifact:
            allowed_image = Path(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        if not allowed_image:
            out.append(Finding(path, "unreviewable-binary"))
        # Scan bytes for known tokens and secrets even in approved images.
        if any(token.encode() in data for token in tokens):
            out.append(Finding(path, "local-denylist"))
        if b"Exif" in data or b"eXIf" in data:
            out.append(Finding(path, "image-metadata"))
        return out
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        return out + [Finding(path, "unknown-encoding")]
    out.extend(text_findings(path, text, tokens))
    out.extend(cache_findings(path, data))
    if not artifact and len(data) > 50000 and "const DATA = {" in text and "<html" in text.lower():
        out.append(Finding(path, "generated-dashboard"))
    if path.endswith(".json"):
        try:
            obj = json.loads(text)
        except ValueError:
            obj = None
        if isinstance(obj, dict) and isinstance(obj.get("files"), dict) and "version" in obj:
            if path != "samples/portfolio.snapshot.json":
                out.append(Finding(path, "unapproved-portfolio-snapshot"))
        if not artifact and isinstance(obj, dict) and not path.startswith(("samples/", "tests/fixtures/")):
            if ("transactions" in obj and any(k in obj for k in ("holdings", "retirement_meta", "basis_totals"))):
                out.append(Finding(path, "private-ledger-export"))
    return out


def blobs(root: Path, entries: list[tuple[str, str, str]]) -> dict[str, bytes]:
    ids = list(dict.fromkeys(oid for _, oid, _ in entries))
    if not ids:
        return {}
    raw = git(root, "cat-file", "--batch", input=("\n".join(ids) + "\n").encode())
    result, offset = {}, 0
    for oid in ids:
        end = raw.index(b"\n", offset)
        header = raw[offset:end].split()
        if len(header) != 3 or header[1] != b"blob":
            raise ValueError("uninspectable git object")
        size = int(header[2])
        result[oid] = raw[end + 1:end + 1 + size]
        offset = end + size + 2
    return result


def index_entries(root: Path) -> list[tuple[str, str, str]]:
    out = []
    for row in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not row:
            continue
        meta, path = row.split(b"\t", 1)
        mode, oid, stage = meta.decode().split()
        if stage != "0":
            raise ValueError("index has unresolved merge entries")
        out.append((mode, oid, os.fsdecode(path)))
    return out


def tree_entries(root: Path, revision: str) -> list[tuple[str, str, str]]:
    out = []
    for row in git(root, "ls-tree", "-r", "-z", "--full-tree", revision).split(b"\0"):
        if row:
            meta, path = row.split(b"\t", 1)
            mode, kind, oid = meta.decode().split()
            if kind != "blob":
                raise ValueError("uninspectable git tree entry")
            out.append((mode, oid, os.fsdecode(path)))
    return out


def scan_entries(root: Path, entries: list[tuple[str, str, str]], tokens: list[str],
                 seen: set[tuple[str, str, str]] | None = None, *,
                 cache: dict | None = None, historical: bool = False) -> list[Finding]:
    seen = seen if seen is not None else set()
    todo = [(mode, oid, path) for mode, oid, path in entries if (mode, oid, path) not in seen]
    data = blobs(root, todo)
    out = []
    for mode, oid, path in todo:
        seen.add((mode, oid, path))
        out.extend(inspect_file(path, data[oid], mode, tokens))
    cache = cache if cache is not None else {}
    needed = [entry for entry in entries if entry[2] in {SAMPLE_PATH, SAMPLE_SOURCE, IMAGE_REGISTRY}
              or entry[2].startswith(("src/", "tools/", "docs/img/", "samples/"))]
    asset_key = ("assets", historical, tuple(needed))
    if asset_key not in cache:
        asset_data = blobs(root, needed)
        files = {path: (mode, asset_data[oid]) for mode, oid, path in needed}
        cache[asset_key] = verify_public_assets(root, files, cache, historical=historical)
    out.extend(cache[asset_key])
    return out


def scan_scope(root: Path, scope: str, tokens: list[str]) -> list[Finding]:
    if scope in {"staged", "tracked"}:
        entries = index_entries(root) if scope == "staged" else tree_entries(root, "HEAD")
        findings = scan_entries(root, entries, tokens)
        if scope == "staged":
            findings.extend(prospective_identities(root, tokens))
        if not findings:
            data = blobs(root, entries)
            record_verified_assets(root, {path: (mode, data[oid]) for mode, oid, path in entries})
        return findings
    names = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    out = []
    files = {}
    for name in sorted(set(names.split(b"\0")) - {b""}):
        path = os.fsdecode(name)
        p = root / path
        if p.is_symlink():
            out.extend(inspect_file(path, os.readlink(p).encode(), "120000", tokens))
        elif p.is_file():
            data = p.read_bytes()
            files[path] = ("100644", data)
            out.extend(inspect_file(path, data, "100644", tokens))
    out.extend(verify_public_assets(root, files, {}))
    if not out:
        record_verified_assets(root, files)
    return out


def scan_commits(root: Path, revisions: list[str], tokens: list[str]) -> list[Finding]:
    seen = set()
    cache = {}
    out = []
    for revision in revisions:
        message = git(root, "show", "-s", "--format=%B", revision).decode("utf-8", errors="replace")
        found = text_findings("<commit-message>", message, tokens)
        identities = git(root, "show", "-s", "--format=%an%n%ae%n%cn%n%ce", revision).decode("utf-8", errors="replace")
        found.extend(text_findings("<commit-identity>", identities, tokens))
        found.extend(scan_entries(root, tree_entries(root, revision), tokens, seen,
                                  cache=cache, historical=True))
        out.extend(Finding(f.path, f.rule, revision[:12]) for f in found)
    return out


def prospective_identities(root: Path, tokens: list[str]) -> list[Finding]:
    """Check the identities Git will record, including environment overrides."""
    findings = []
    for name in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
        identity = git(root, "var", name).decode("utf-8", errors="replace")
        findings.extend(text_findings("<prospective-commit-identity>", identity, tokens))
    return findings


def push_refs(input: str) -> list[list[str]]:
    refs = []
    for line in input.splitlines():
        fields = line.split()
        if len(fields) != 4:
            raise ValueError("malformed pre-push ref description")
        _, local, _, remote = fields
        if not re.fullmatch(r"[0-9a-f]{40,64}", local) or not re.fullmatch(r"[0-9a-f]{40,64}", remote):
            raise ValueError("malformed pre-push object id")
        if set(local) == {"0"}:
            continue  # ref deletion introduces no content
        refs.append(fields)
    return refs


def advertised_commits(root: Path, destination: str) -> list[str]:
    """Use only the exact push destination's current advertisement as proof."""
    advertised = git(root, "ls-remote", "--refs", "--", destination)
    known = []
    for line in advertised.splitlines():
        oid = line.split(b"\t", 1)[0].decode("ascii")
        if not re.fullmatch(r"[0-9a-f]{40,64}", oid):
            raise ValueError("invalid destination advertisement")
        # Unavailable remote objects are not assumed safe or silently fetched.
        result = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", oid + "^{commit}"],
                                capture_output=True)
        if result.returncode == 0:
            known.append(result.stdout.decode().strip())
    return known


def outgoing(root: Path, input: str, destination: str | None = None) -> list[str]:
    revisions = []
    refs = push_refs(input)
    published = advertised_commits(root, destination) if destination and any(set(ref[3]) == {"0"} for ref in refs) else []
    for _, local, _, remote in refs:
        # For new refs, exclude only commits proven present by the exact
        # destination's current advertisement. Without that evidence scan all
        # ancestry; stale remote-tracking refs never establish publication.
        args = ([local, "--not", *published] if published else [local]) if set(remote) == {"0"} else [f"{remote}..{local}"]
        revisions.extend(git(root, "rev-list", "--reverse", *args).decode().splitlines())
    return list(dict.fromkeys(revisions))


def scan_push(root: Path, input: str, tokens: list[str], destination: str | None = None) -> list[Finding]:
    findings = []
    seen_tags = {}
    for local_ref, local, remote_ref, _ in push_refs(input):
        for ref in (local_ref, remote_ref):
            findings.extend(text_findings("<ref-name>", ref, tokens))
        obj = local
        while git(root, "cat-file", "-t", obj).strip() == b"tag":
            if obj in seen_tags:
                obj = seen_tags[obj]
                continue
            tag_object = obj
            contents = git(root, "cat-file", "tag", obj).decode("utf-8", errors="replace")
            header, _, annotation = contents.partition("\n\n")
            findings.extend(text_findings("<tag-annotation>", annotation, tokens))
            fields = dict(line.split(" ", 1) for line in header.splitlines() if " " in line)
            findings.extend(text_findings("<tag-name>", fields.get("tag", ""), tokens))
            findings.extend(text_findings("<tagger-identity>", fields.get("tagger", ""), tokens))
            obj = fields.get("object", "")
            if not re.fullmatch(r"[0-9a-f]{40,64}", obj):
                raise ValueError("malformed annotated tag object")
            seen_tags[tag_object] = obj
        if git(root, "cat-file", "-t", obj).strip() != b"commit":
            findings.append(Finding("<push-ref>", "unsupported-push-object"))
    findings.extend(scan_commits(root, outgoing(root, input, destination), tokens))
    return findings


def scan_artifact(root: Path, target: Path, tokens: list[str]) -> list[Finding]:
    target = target.resolve()
    index = target / "index.html" if target.is_dir() else target
    directory = index.parent
    try:
        manifest = json.loads((directory / "provenance.json").read_text())
        html = index.read_text()
        start = html.index("const DATA = ") + len("const DATA = ")
        data, _ = json.JSONDecoder().raw_decode(html[start:])
        demo = data["demo"]
        expected_source = "tools/build_sample_snapshot.py"
        valid = (manifest.get("schema") == 1 and manifest.get("synthetic") is True
                 and demo.get("synthetic") is True and demo.get("source") == expected_source
                 and demo.get("source_sha256") == hashlib.sha256((root / expected_source).read_bytes()).hexdigest())
        for key in ("as_of", "source", "source_sha256", "snapshot_sha256"):
            valid = valid and manifest.get(key) == demo.get(key)
        files = manifest["files"]
        valid = valid and isinstance(files, dict) and "index.html" in files
        for name, digest in files.items():
            if PurePosixPath(name).name != name or name in {".", "..", "provenance.json"}:
                valid = False
                continue
            p = directory / name
            valid = valid and not p.is_symlink() and hashlib.sha256(p.read_bytes()).hexdigest() == digest
        actual = {p.name for p in directory.iterdir()}
        valid = valid and actual == set(files) | {"provenance.json"}
        if not valid:
            raise ValueError("invalid provenance")
        # Matching claims and hashes only prove internal consistency. Rebuild
        # independently and compare the actual bytes before approving content.
        expected = canonical_outputs(root, "demo", {})
        if actual != set(expected) or any((directory / name).read_bytes() != data
                                          for name, data in expected.items()):
            raise ValueError("artifact differs from independent synthetic build")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return [Finding("<demo-artifact>", "unverified-demo-provenance")]
    out = []
    for name in sorted(actual):
        p = directory / name
        out.extend(inspect_file(name, p.read_bytes(), "100644", tokens, artifact=True))
    return out


def initialize(root: Path) -> None:
    path = root / ".pii-denylist.txt"
    if path.exists():
        print("privacy: existing local denylist preserved")
        return
    # Keep local configuration local; never echo the identity being seeded.
    proc = subprocess.run(["git", "-C", str(root), "config", "user.email"], capture_output=True, text=True)
    email = proc.stdout.strip() if proc.returncode == 0 else ""
    match = EMAIL.fullmatch(email)
    seed = email if match and match.group(1).lower() not in SAFE_MAIL_DOMAINS else ""
    content = ("# Local privacy denylist, one high-precision token per line. Never commit.\n"
               "# Add private emails, account identifiers, and distinctive financial values.\n"
               "# Public/example identifiers need not be listed. Values are never printed.\n")
    if seed:
        content += seed + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(content)
    print("privacy: initialized ignored local denylist; review/add private tokens locally")


def print_findings(findings: list[Finding], tokens: list[str]) -> None:
    for f in sorted(set(findings), key=lambda x: (x.revision, x.path, x.rule)):
        safe_path = "<redacted-path>" if text_findings("", f.path, tokens) else f.path
        for token in tokens:
            safe_path = re.sub(re.escape(token), "[redacted]", safe_path, flags=re.I)
        safe_path = "".join(c if ord(c) >= 32 else "?" for c in safe_path)
        print(f"privacy: BLOCKED {f.rule}: {safe_path}" + (f" (commit {f.revision})" if f.revision else ""), file=sys.stderr)
    if findings:
        print("privacy: values and matching lines are intentionally withheld; review locally, do not bypass", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    scan = sub.add_parser("scan")
    group = scan.add_mutually_exclusive_group(required=True)
    group.add_argument("--scope", choices=("worktree", "staged", "tracked"))
    group.add_argument("--commits")
    group.add_argument("--artifact", type=Path)
    group.add_argument("--message-file", type=Path)
    scan.add_argument("--require-local", action="store_true")
    push = sub.add_parser("pre-push")
    push.add_argument("--remote", help="Exact destination supplied by Git's pre-push hook")
    args = parser.parse_args(argv)
    try:
        root = repository(args.repo)
        if args.command == "init":
            initialize(root)
            return 0
        tokens = load_tokens(root, args.command == "pre-push" or getattr(args, "require_local", False))
        if args.command == "pre-push":
            findings = scan_push(root, sys.stdin.read(), tokens, args.remote)
        elif args.scope:
            findings = scan_scope(root, args.scope, tokens)
        elif args.commits:
            revisions = git(root, "rev-list", "--reverse", args.commits).decode().splitlines()
            findings = scan_commits(root, revisions, tokens)
        elif args.artifact:
            findings = scan_artifact(root, args.artifact, tokens)
        else:
            findings = text_findings("<commit-message>", args.message_file.read_text(), tokens)
            findings.extend(prospective_identities(root, tokens))
        print_findings(findings, tokens)
        if findings:
            return 1
        print("privacy: PASS (inspected content; no matching rules)")
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print("privacy: inspection could not complete; refusing approval. " + str(exc) if isinstance(exc, ValueError)
              else "privacy: inspection could not complete; refusing approval", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
