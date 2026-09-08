# Privacy and publication

finledger is a public code repository that processes private financial files.
The public demo is a separate, deterministic build from fictional inputs. A
personal dashboard includes the entire ledger in its HTML, even when a tab or
column is hidden. Hiding a field does not remove it from the file.

## Local boundaries

Keep raw exports and metadata in `data/`, generated dashboards in `exports/`,
and investigation notes/logs in `audit/` or a temporary directory. These paths
are ignored by Git. Never force-add them. Personal snapshots, account identifiers,
private email addresses, financial values, tax documents, and screenshots of a
personal dashboard must not appear in source, tests, docs, commit messages,
issue/PR text, or external tools.

Generated price shards, the legacy price cache, and price coverage metadata
also stay local (`cache/prices/`, `cache/price_cache.json`, and
`cache/price_cache_meta.json`). Removing them from version control does not
require deleting local files: use `git rm --cached` for that migration.
Before updating another clone that still tracks these files, privately back up
its prices and coverage metadata together; pulling their removal can delete
unchanged tracked copies. Restore the backup afterward under the ignored paths.
Keep the local denylist intact; moving caches out of publication avoids
weakening protection for matching private values.

Normal ingestion uses yfinance for symbol price/sector requests. Broker files
and the complete ledger are not uploaded by that process. Public demo generation
uses isolated paths, fictional price curves, a fixed date, and disabled network
access. Use that builder for every public screenshot and published artifact.

## Install the guards once per clone

```bash
sh tools/install-hooks.sh
```

This configures `core.hooksPath=githooks`, enables pre-commit, commit-message,
and pre-push hooks, and initializes `.pii-denylist.txt` if absent. It preserves an
existing denylist and never prints its values. A locally configured non-example
Git email may be seeded automatically; review the list in a local editor.

Use your GitHub-provided `users.noreply.github.com` address for public commits.
Configure it locally for this repository if needed. Author, committer, and tagger
identities are published metadata: the guard checks them as well as message text.
The staged and commit-message checks reject a prospective private email before
the commit is created. Installing hooks does not change your Git identity.

Add high-precision private tokens: email addresses, account identifiers, and
distinctive financial values. Avoid common round numbers. One token goes on each
line; lines beginning with `#` are comments. Numeric matching recognizes comma
formatting and trailing decimal zeros. Local tokens apply to market caches too;
there is no blanket cache exemption. The file is ignored and explicitly blocked
from publication. Never paste it into a conversation, issue, or tracked fixture.

Hooks require this local file to exist. CI cannot use the private denylist, so it
independently enforces generic/structural rules and synthetic artifact provenance.
A local list with no tokens still runs those rules, but cannot match unknown
private values. Populate it locally as private inputs are introduced.

## Before every commit

1. Confirm all changed fixtures, expected values, documents, and screenshots
   were created independently from fictional data. Inspect screenshots visually.
2. Review `git status --short` and `git diff`, then stage explicit intended paths.
   Avoid broad staging when working with personal inputs.
3. Inspect `git diff --cached` and run the staged scan below. The scan reads the
   **complete index**, not just newly added lines or the working copy.
4. Write and review a qualitative commit message. Scan it before committing;
   the commit-message hook also checks the actual message Git will record.

```bash
uv run python -m tools.privacy_guard scan --scope staged --require-local
uv run python -m tools.privacy_guard scan --message-file /tmp/finledger-message.txt --require-local
git commit -F /tmp/finledger-message.txt
```

The guard rejects protected paths even when force-added. It checks local tokens,
common credential/identifier patterns, non-example email addresses, personal
absolute paths, unexpected binary files, personal cache anchors, malformed
market-price cache shapes, and private ledger/snapshot files. Diagnostics identify
a rule and a redacted location, never the matching private text.

If a scan blocks, inspect the content locally and remove/sanitize it. If an
independently verified public value collides with a denylist entry, refine that
entry deliberately. Do not disable hooks or bypass verification. A failed scan
or failed Git inspection is a failure, not a pass.

## Before every push

Review the destination, outgoing commits, their diffs, and their messages. A
clean final tree is insufficient: an intermediate commit can contain information
that a later commit deletes.

```bash
# Use the actual existing destination ref; this example compares against main.
uv run python -m tools.privacy_guard scan --commits origin/main..HEAD --require-local
git push
```

The pre-push hook reads Git's exact local and remote object IDs from stdin. It
scans every outgoing commit tree and message, including intermediate content,
ref names, annotated tags, and author/committer/tagger identities. For a new ref it queries the exact destination's
current advertisement and excludes only published commits available locally.
Without that evidence, history is scanned back to its root; a stale tracking
branch is not evidence. Deleted refs introduce no new content. Failed destination
queries, missing objects, or malformed ref input fail closed. Fetch the actual
destination ref and inspect again if necessary. Never rewrite shared history
automatically to work around a privacy failure.

There is no repository code that automatically commits or pushes changes. The
hooks are a second check after the explicit review above.

## Before publishing a demo or screenshot

```bash
uv run python -m tools.build_demo --output _site
npm test
uv run python -m tools.privacy_guard scan --artifact _site --require-local
```

The builder never imports personal `data/` or mutable `cache/`. Its artifact
manifest records the generator source, fictional snapshot, and output hashes.
The scanner checks manifest/embedded-data agreement, source hashes, the complete
artifact file inventory, and content rules. It also copies the current public
source into a temporary project, independently rebuilds the synthetic demo, and
requires byte-for-byte agreement. Self-consistent hashes on a substituted
personal dashboard are insufficient. CI validates the final bannered artifact,
then deploys that same validated artifact.

A provenance marker is not a magic sanitizer. Its evidence comes from the
reviewed generator and isolated build. The sample snapshot must independently
match the current generator. Staged source must match the reviewed working
source; historical code is never executed during inspection. Successful current
asset verification records local content fingerprints in the ignored
`.privacy-receipts.json`. A later outgoing commit can reuse an exact receipt for
an earlier verified synthetic asset version; its files, messages, and identities
still undergo privacy scanning. Receipts contain hashes, not financial inputs,
and are blocked from publication. Do not hand-edit them or copy them between
untrusted clones. Older unpublished variants without a matching receipt fail
conservatively and need deliberate local review.

Keep generated demo output ignored; commit the generator and sample fixtures
instead. Images under `docs/img/` require a matching provenance registry, exact
image/source/artifact hashes, and explicit visual review. The capture tool resets
`visually_reviewed` to `false` on regeneration. Inspect all images before setting
it to `true`. The flag records a human review; an automated text scan cannot read
financial values painted into pixels. Image metadata must not carry personal
information. Recreate screenshots from the fictional demo rather than blurring a
personal screenshot. Changes to the rendered dashboard require refreshed images
and their review record before committing.

## Limits and existing history

Automated scanning cannot decide whether an arbitrary number is personal, prove
that every fixture is fictional, detect all possible secrets, or perform OCR on
images. Keep provenance, local token maintenance, human review, hooks, and CI as
separate checks. Do not describe a passing scan as a guarantee of no possible
leak.

The guard scans the content selected by its scope. An ordinary staged scan does
not audit every historical commit. To investigate existing history, explicitly
scan the relevant range or all revisions locally and handle findings without
copying the matching content into public reports. Previously disclosed secrets
must be revoked through their provider; deleting them in a new commit does not
remove their history.
