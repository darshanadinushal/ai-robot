# Shared by upgrade-pi.sh and rollback-pi.sh (sourced, not run).
# LUNA_PYTHON: the Python to use; LUNA_PIP=skip: do not reinstall the Python packages;
# LUNA_STATE_DIR: where version notes and backups are kept (default ~/.luna).
STATE="${LUNA_STATE_DIR:-$HOME/.luna}"
PY="${LUNA_PYTHON:-python3}"
[ -z "${LUNA_PYTHON:-}" ] && [ -x .venv/bin/python ] && PY=.venv/bin/python

say() { printf '%s\n' "$*"; }

require_clean_tree() {
  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    say "Local changes in $(pwd): commit or stash them first (git stash)."
    exit 1
  fi
}

backup_data() {
  # Settings, memories and plugin choices; restored only by: rollback-pi.sh --with-data
  local folder="$STATE/backups/$(date +%Y%m%d-%H%M%S)-$(git rev-parse --short HEAD)"
  mkdir -p "$folder"
  local file
  for file in "$HOME/.ai-robot-hud.json" "${LUNA_MEMORY_FILE:-$HOME/.luna-memory.db}" \
              "${LUNA_PLUGINS_FILE:-$HOME/.luna/plugins.json}"; do
    if [ -f "$file" ]; then cp -p "$file" "$folder/"; fi
  done
  printf '%s\n' "$folder" > "$STATE/last-backup"
  say "Backup: $folder"
}

switch_to() {
  # A tag (v1.0.0), branch (main) or commit; branches are brought up to date.
  local target="$1"
  if git remote get-url origin >/dev/null 2>&1; then
    git fetch --quiet --tags origin || say "Could not reach GitHub; using what is here."
  fi
  git checkout --quiet "$target"
  if git show-ref --verify --quiet "refs/remotes/origin/$target"; then
    git merge --quiet --ff-only "origin/$target"
  fi
}

install_packages() {
  if [ "${LUNA_PIP:-}" = "skip" ] || [ ! -x .venv/bin/pip ]; then
    return 0
  fi
  # Luna itself without dependencies (pygame comes from apt on the Pi), then the [voice] and
  # [ai] extras listed in pyproject.toml.
  .venv/bin/pip install --quiet --no-deps -e . &&
    .venv/bin/python -c 'import tomllib; e = tomllib.load(open("pyproject.toml", "rb"))["project"]["optional-dependencies"]; print("\n".join(e["voice"] + e["ai"]))' |
    xargs .venv/bin/pip install --quiet
}

check_start() {
  # The version, then one headless frame with mock hardware (no screen, microphone or AI).
  local shot
  shot="$(mktemp -d)/luna-check.png"
  "$PY" -m luna_core --version &&
    SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy LUNA_LOG_FILE=off \
      "$PY" -m luna_core --headless --mock-hardware --screenshot "$shot" >/dev/null 2>&1 &&
    [ -s "$shot" ]
}

restart_service() {
  if command -v systemctl >/dev/null 2>&1 && systemctl --user is-active --quiet luna.service 2>/dev/null; then
    systemctl --user restart luna.service
    say "Luna restarted."
  fi
}

version_name() {
  git describe --tags --always 2>/dev/null || git rev-parse --short HEAD
}
