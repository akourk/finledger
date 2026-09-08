# Working on finledger

@AGENTS.md

The shared project instructions above apply to Claude Code and other agents.
Use [CONTRIBUTING.md](CONTRIBUTING.md#find-the-right-surface) to select source,
tests, and a focused skill. Read only the relevant sections of
[docs/INVARIANTS.md](docs/INVARIANTS.md), where the detailed reference now lives.
Do not import that entire reference into this entrypoint: most changes need
only one subsystem's rules. Existing section links below remain valid.

## How to read it

See [reference navigation](docs/INVARIANTS.md#how-to-read-it).

## How to maintain it

Keep shared instructions in `AGENTS.md`, workflows in `CONTRIBUTING.md`, user
instructions in `docs/USAGE.md`, and subsystem rules in `docs/INVARIANTS.md`.
See [reference maintenance](docs/INVARIANTS.md#how-to-maintain-it).

## What this project is

See [project orientation](docs/INVARIANTS.md#what-this-project-is).

## Run it

Use [the isolated development loop](CONTRIBUTING.md#development-loop).

## Pipeline (read `src/main.py` top to bottom — it is the spec)

See [pipeline semantics](docs/INVARIANTS.md#pipeline).

## Invariants the code relies on

See [ledger invariants](docs/INVARIANTS.md#invariants-the-code-relies-on).

## Adding a new broker

Use [fin-add-broker](.claude/skills/fin-add-broker/SKILL.md).

## Adding a new normalized action

Use [fin-add-action](.claude/skills/fin-add-action/SKILL.md).

## Architecture (post-refactor)

See [the module and consumer map](docs/INVARIANTS.md#architecture-post-refactor).

## Module reset functions (for tests)

See [test isolation](docs/INVARIANTS.md#module-reset-functions-for-tests).

## Invariants the cost-basis walker relies on

See [cost-basis invariants](docs/INVARIANTS.md#invariants-the-cost-basis-walker-relies-on)
and use [fin-lot-walker-sync](.claude/skills/fin-lot-walker-sync/SKILL.md).

## Invariants the price cache relies on

See [price-cache invariants](docs/INVARIANTS.md#invariants-the-price-cache-relies-on).

## Files that *look* like they could be shared infrastructure but aren't

See [local caches and metadata](docs/INVARIANTS.md#local-cache-and-metadata-files).

## Repairing a suspect price cache

See [cache repair](docs/INVARIANTS.md#repairing-a-suspect-price-cache).

## Snapshot feature

See [snapshot boundaries](docs/INVARIANTS.md#snapshot-feature).

## Data is sensitive

Read [docs/PRIVACY.md](docs/PRIVACY.md) before preparing a commit or push.

## Adding sample data

See [sample generation](docs/INVARIANTS.md#adding-sample-data).
