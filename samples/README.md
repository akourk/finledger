# Fictional sample portfolio

`portfolio.snapshot.json` belongs to **Sam Sample**, an invented person. Every
transaction, account balance, income figure, birthday, and budget entry is
fictional. `prices.fixture.json` defines illustrative price curves, not observed
market prices. Familiar public ticker names are labels for the example.

The snapshot is fixed at **June 30, 2026**. Its eleven CSV inputs exercise broker
parsing, transfers, FIFO/HIFO lots, an option exercise, a split followed by a
sale, a custodial rollover, and a savings balance anchor. Five reconciliation
checks match; one explicitly explained earlier-close statement difference
shows how an auditable exception works.

Build and view the public demo safely:

```bash
uv sync --frozen --all-groups
uv run --frozen python -m tools.build_demo
# Open _site/index.html in your browser.
```

The builder creates fresh temporary data, cache, and export directories; it
never reads your `data/`, `cache/`, `exports/`, or environment-configured
portfolio paths. It blocks network access and fails on any attempted pricing
fallback. Only the final bannered page and a manifest of its input and output
hashes enter `_site/`. Rebuilding reproduces the same artifact.

To edit the fictional transactions, change `tools/build_sample_snapshot.py`,
then regenerate the checked-in snapshot:

```bash
uv run --frozen python -m tools.build_sample_snapshot
uv run --frozen python -m tools.build_demo
uv run --frozen python -m tools.privacy_guard scan --artifact _site
npm ci
npm test
```

Statement target values are fixed fixtures, not values copied from the current
pipeline output at build time. A financial regression therefore fails the
build instead of silently teaching the sample to agree with itself.

Do not replace sample fixtures with real exports, downloaded prices from a
personal cache, or screenshots of a personal dashboard. See
[Privacy](../docs/PRIVACY.md) for the repository and commit/push safeguards.
