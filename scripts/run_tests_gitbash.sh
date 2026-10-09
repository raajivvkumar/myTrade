#!/usr/bin/env bash
# Runs local pytest without relying on a possibly inaccessible Windows %TEMP%/pytest-of-USER.
# All temporary fixtures are synthetic test inputs, not broker data archives.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

# On Windows Git Bash this resolves to the repository's D: drive, not C:\Users\...\Temp.
scratch_parent="$(cd "$repo_root/.." && pwd)"
scratch="$(mktemp -d "${scratch_parent}/myTrade-pytest-XXXXXXXX")"
if [[ ! -d "$scratch" ]]; then
  echo "Cannot create isolated pytest scratch directory" >&2
  exit 1
fi

cleanup() {
  if [[ -n "${scratch:-}" && -d "$scratch" ]]; then
    rm -rf -- "$scratch"
  fi
}
trap cleanup EXIT

echo "Running offline tests using temporary scratch: $scratch"
# --basetemp overrides pytest's default inaccessible %TEMP%/pytest-of-USER path.
# Disable pytest's repository cache; source and market data remain untouched.
python -m pytest -q -p no:cacheprovider --basetemp="$scratch" "$@"
