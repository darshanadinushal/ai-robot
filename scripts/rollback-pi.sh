#!/usr/bin/env bash
# Go back to the version Luna had before the last upgrade (run again to undo the rollback).
#   bash scripts/rollback-pi.sh              code only
#   bash scripts/rollback-pi.sh --with-data  also restore the settings, memories and plugin
#                                            choices saved by the last upgrade
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/deploy-common.sh

main() {
  if [ ! -f "$STATE/previous-version" ]; then
    say "No earlier version is known (upgrade with scripts/upgrade-pi.sh first)."
    exit 1
  fi
  require_clean_tree
  local previous current
  previous="$(cat "$STATE/previous-version")"
  current="$(git rev-parse HEAD)"
  say "Luna $(version_name) -> $(git rev-parse --short "$previous")"
  git checkout --quiet "$previous"
  install_packages
  if [ "${1:-}" = "--with-data" ] && [ -f "$STATE/last-backup" ]; then
    local folder file
    folder="$(cat "$STATE/last-backup")"
    shopt -s dotglob  # the backups are dotfiles (.ai-robot-hud.json, .luna-memory.db)
    for file in "$folder"/*; do
      [ -e "$file" ] || continue
      case "$(basename "$file")" in
        .ai-robot-hud.json) cp -p "$file" "$HOME/" ;;
        plugins.json) cp -p "$file" "${LUNA_PLUGINS_FILE:-$HOME/.luna/plugins.json}" ;;
        *) cp -p "$file" "${LUNA_MEMORY_FILE:-$HOME/$(basename "$file")}" ;;
      esac
    done
    say "Restored data from $folder"
  fi
  printf '%s\n' "$current" > "$STATE/previous-version"
  if check_start; then
    say "Now on $(version_name)."
  else
    say "Warning: this version did not pass the start check."
  fi
  restart_service
}

main "$@"; exit $?
