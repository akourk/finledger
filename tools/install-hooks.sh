#!/bin/sh
# Install per-clone privacy gates. Existing local denylist entries are preserved.
set -eu
root=$(git rev-parse --show-toplevel)
cd "$root"
sh tools/run-privacy-guard.sh init
git config core.hooksPath githooks
echo 'Enabled pre-commit, commit-message, and pre-push privacy checks.'
