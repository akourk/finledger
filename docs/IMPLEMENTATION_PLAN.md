# Application and portfolio improvement plan

This plan follows the September 2026 review. Every example, fixture, and public
artifact must use fictional data. Completion means the behavior is implemented
and checked, not merely documented.

## 1. Protect inputs and financial calculations

- [x] Escape embedded JSON and all imported text rendered as HTML.
- [x] Apply historical cutoffs consistently to holdings, realized gains, and risk.
- [x] Aggregate monthly observations before calculating monthly risk ratios.
- [x] Validate account classifications and provide a starter mapping command.
- [x] Reject malformed/nonfinite inputs before publishing; preserve last good outputs.
- [x] Make dry-run read-only for every rename outcome.
- [x] Normalize crypto identity by broker and report unsupported trade valuations.
- [x] Validate the whole snapshot before restoring files.

## 2. Make dashboard interactions reliable

- [x] Preserve input focus and partially entered values in Planning and Tax.
- [x] Make disclosures, filters, and column controls keyboard operable.
- [x] Show retryable render failures and test meaningful content in each tab.
- [x] Use readable column labels and consistent date/currency presentation.
- [x] Fix mobile overflow and reduce header/navigation density.

## 3. Build and validate a reproducible public demonstration

- [x] Build fixed-date fictional inputs and illustrative prices in isolated directories.
- [x] Resolve accidental sample integrity issues; explain deliberate diagnostics.
- [x] Add introduction, source/case-study links, sample date, favicon, and sharing metadata.
- [x] Declare and lock Python/Node dependencies with clean setup instructions.
- [x] Run typing, keyboard, historical-date, export, and responsive browser checks.
- [x] Validate the exact published artifact; require successful checks before deployment.

## 4. Guard privacy and explain the engineering

- [x] Scan full staged content, sensitive paths, messages, and every outgoing commit.
- [x] Add pre-commit, commit-message, and pre-push hooks with redacted diagnostics.
- [x] Verify fixture provenance and scan final demo artifacts before publication.
- [x] Add synthetic tests proving privacy guards reject leaks and cannot silently skip.
- [x] Document safe contribution/release procedures and known limits of automated scanning.
- [x] Shorten README; preserve the detailed usage reference and add an engineering case study.
- [x] Run the full regression suite, browser validation, and final privacy inspection.

No production data should be used to reproduce a failure, create a screenshot,
write documentation, or establish expected test values. Before any commit or
push, inspect the exact content being published and run the privacy guard.


## Implemented behavior and release requirements

The work above is implemented using fictional fixtures and isolated pipeline
runs. Account types are now mandatory; use `--init-account-mappings` and review
its output before ingestion. Ambiguous manual crypto symbols require explicit
`-USD` identifiers, and unsupported crypto-to-crypto valuation fails visibly.

Public builds use the fixed 2026-06-30 sample with illustrative prices. Deployment
requires the validated artifact, and screenshot provenance requires human review.
The privacy hooks are installed per clone and inspect public files, messages,
Git identities, tags, and outgoing history. Use a GitHub no-reply commit email
and maintain private denylist values locally. See [Privacy](PRIVACY.md).

The table below records local pre-publication validation. GitHub Actions reports
validation and deployment status separately for each published commit.

## Validation completed 6 September 2026

| Check | Result |
| --- | --- |
| Full suite, Python 3.12 with the exact locked dependency graph | 1,405 passed; 1 expected skip because private local metadata is absent |
| Final demo browser checks | All ten tabs, individual typing, decimal inputs, keyboard disclosures, historical gains, monthly risk parity, CSV download, deep links, and mobile layout pass |
| Final demo accessibility | Twelve states pass the serious/critical axe gate |
| Public artifact | Independent isolated rebuild matches its bytes; complete artifact privacy scan passes |
| Public worktree | Local-denylist, structural, content, sample, and reviewed-screenshot checks pass |
| Fresh temporary public checkout | Installed hooks, full staged commit, commit-message check, and CI tracked scan pass using a fictional Git identity |
| Privacy boundary regressions | Private identities, intermediate commits, tags, changed file modes, substituted artifacts, and stale screenshots block; separately verified outgoing asset versions remain usable |
| Visual inspection | Four README screenshots reviewed; mobile page width equals its 375-pixel viewport |
| Repository hygiene | Diff whitespace checks pass; private data and mutable caches were not used for validation |

The remote Python 3.10/3.12 workflow and Pages deployment enforce their own gates.
The browser checks above used local Chrome. See [Demo](DEMO.md) for reproducible
commands and artifact scope.
