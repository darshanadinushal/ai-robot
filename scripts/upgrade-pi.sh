#!/usr/bin/env bash
# Upgrade Luna to a version: a tag (v1.0.1), a branch (main) or a commit.
#   bash scripts/upgrade-pi.sh v1.0.1
# Backs up settings, memories and plugin choices, installs the packages and checks that Luna
# starts. If the check fails, Luna goes straight back to the version she had.
# Undo a working upgrade later with: bash scripts/rollback-pi.sh
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/deploy-common.sh

main() {
  local target="${1:-}"
  if [ -z "$target" ]; then
    say "Usage: bash scripts/upgrade-pi.sh <version, for example v1.0.1 or main>"
    exit 2
  fi
  require_clean_tree
  mkdir -p "$STATE"
  local current earlier=""
  current="$(git rev-parse HEAD)"
  [ -f "$STATE/previous-version" ] && earlier="$(cat "$STATE/previous-version")"
  say "Luna $(version_name) -> $target"
  backup_data
  if switch_to "$target" && install_packages && check_start; then
    printf '%s\n' "$current" > "$STATE/previous-version"
    say "Upgraded to $(version_name)."
    restart_service
    return 0
  fi
  say "The new version did not start: going back to $(git rev-parse --short "$current")."
  git checkout --quiet --force "$current"
  install_packages || true
  if [ -n "$earlier" ]; then printf '%s\n' "$earlier" > "$STATE/previous-version"; fi
  restart_service
  return 1
}

# The whole script is read before git changes this file.
main "$@"; exit $?
