# Sample portfolio

`portfolio.snapshot.json` is a **fully synthetic, fictional portfolio**. It is
the only dataset that ships with this repository, and it is what the
[live demo](https://akourk.github.io/finledger/) and the CI accessibility check
are built from.

**Nothing in it is real.** It belongs to "Sam Sample", a fictional person born
1990-06-15 with an invented salary history. Every balance, trade, contribution
and dollar figure is made up, and the round numbers are deliberate — they are
meant to look synthetic at a glance. Real broker exports and the dashboards
built from them never enter this repository: `data/` and `exports/` are
gitignored, and a pre-commit hook blocks real figures from reaching a tracked
file. See [Privacy](../README.md#privacy).

## What it is

A [snapshot](../src/snapshot.py) — one JSON file bundling eleven CSVs, one per
supported input format:

```
robinhood-1.csv           coinbase-1.csv            coinbase-pro-gdax-1.csv
schwab-roth-ira-1.csv     vanguard-401k.csv         voya-401k-1.csv
usaa-roth-ira.csv         apple-savings.csv         ExportedTransactions.csv
manual-adjustments.csv    metadata.csv
```

That coverage is the point. The sample is not a minimal example; it is sized to
exercise every parser and most of the interesting downstream paths — a
custodial rollover that needs reconstructing, an option contract, crypto
conversions, a savings account with a balance anchor, per-account lot methods,
and `Reconcile *` rows so the Overview's reconciliation panel has something to
check against.

## Using it

From a fresh checkout:

```bash
python -m src.main --import-snapshot samples/portfolio.snapshot.json
python -m src.main
```

The first command writes the eleven CSVs into `data/`; the second runs the
pipeline and produces `exports/dashboard.html`. Existing files in `data/` are
preserved unless you pass `--force` — so if you already have real data there,
**import into a scratch directory instead** by setting the `FIN_*_DIR`
environment variables (see `src/config.py`).

## Regenerating it

```bash
python tools/build_sample_snapshot.py
```

Re-run this after changing a parser, and then run the pipeline over the result
to confirm the shipped snapshot still parses. The script's per-broker writer
functions double as documentation of each broker's CSV format — they are the
most readable statement of what each parser expects to receive.
