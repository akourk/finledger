#!/bin/sh
# Enable the repo's git hooks (the pre-commit PII guard in githooks/).
#
# core.hooksPath is a per-clone local setting, so it must be re-run on
# every fresh clone / new machine.  Run once from anywhere in the repo:
#
#     sh tools/install-hooks.sh
#
# Then seed .pii-denylist.txt (gitignored) with your real emails /
# account ids / distinctive figures — see githooks/pre-commit.

set -e
root=$(git rev-parse --show-toplevel)
git -C "$root" config core.hooksPath githooks
echo "Enabled core.hooksPath=githooks."

if [ ! -f "$root/.pii-denylist.txt" ]; then
  cat > "$root/.pii-denylist.txt" <<'TEMPLATE'
# Local PII denylist for githooks/pre-commit.  GITIGNORED — never commit.
# One HIGH-PRECISION token per line (emails, account ids, distinctive
# 6-digit or decimal figures).  Avoid short/round numbers — they cause
# false positives.  Add new real figures as you start using them.
TEMPLATE
  echo "Created a starter .pii-denylist.txt — add your real tokens to it."
else
  echo ".pii-denylist.txt already exists — leaving it as-is."
fi
