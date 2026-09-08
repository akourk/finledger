# Working on finledger

This repository is public. User financial data is private.

- Read `docs/PRIVACY.md` before preparing any commit or push. Never bypass a
  privacy hook. Scan the complete staged content and commit message before
  committing, and every outgoing commit before pushing.
- Do not copy personal amounts, identity, account numbers, raw exports, snapshots,
  logs, screenshots, or local filesystem paths into tracked files, commit/PR
  messages, or external services. Use independently constructed fictional data.
- Keep `data/`, `exports/`, `audit/`, local denylists, and generated private
  caches local. `.gitignore` is not protection against force-adding a file.
- Run the pipeline only in temporary directories with all `FIN_*_DIR` paths
  isolated when developing or testing. Build public artifacts with the dedicated
  synthetic demo builder; never publish the result of a personal pipeline run.
- Use the existing regression suite and add behavioral tests for new failure
  boundaries. Keep Python/JavaScript financial calculations in parity.
- Read the relevant sections of [docs/INVARIANTS.md](docs/INVARIANTS.md) before
  changing financial behavior. Use [CONTRIBUTING.md](CONTRIBUTING.md) for setup,
  validation, and the task-to-source/test/skill map; detailed user instructions
  belong in `docs/USAGE.md`.

## Shared financial rules

- Resolve broker direction before converting numeric magnitudes to nonnegative
  values. Use the strict CSV helpers and preserve prior outputs on import errors.
- Keep action and income classification in `src/actions.py`, external cash flow
  in `basis.txn_external_cash_flow`, and position valuation in `valuation.mark`.
- Basis changes must agree across `basis.py`, `history.py`, both pipeline paths,
  and exported annotations. Savings cash basis is contributed principal.
- Compute shared figures once in Python and consume their exported fields.
  Interactive date-window calculations still need matching Python/JS behavior.
  Monthly statistics use monthly observations; as-of answers exclude later data.
- Dashboard source is `src/dashboard/app/*.js`, concatenated in filename order.
  Check the assembled bundle and affected interactions using the fictional demo.

## Agent guidance

Read the skill matching the change from `.claude/skills/`; the mapping in
`CONTRIBUTING.md` works for any agent even when the client does not discover that
directory automatically. Maintain those skills in one place. Keep this file and
`CLAUDE.md` compact; record subsystem rationale in `docs/INVARIANTS.md` alongside
the regression that protects it. Audit history is supporting context, not an
instruction to reproduce investigations against personal data.

Automated scans cannot prove that an arbitrary financial figure is fictional.
Review provenance as well as scan results; do not describe a passing scan as a
guarantee that no personal data could exist.
