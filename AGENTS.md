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
- Consult `CLAUDE.md` for subsystem invariants. Keep contribution instructions in
  `CONTRIBUTING.md` and detailed user instructions in `docs/USAGE.md`.

Automated scans cannot prove that an arbitrary financial figure is fictional.
Review provenance as well as scan results; do not describe a passing scan as a
guarantee that no personal data could exist.
