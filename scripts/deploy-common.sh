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

copy_data() {
  # copy_data FROM TO. A SQLite database (memories, tasks.db) is copied with SQLite's backup
  # API, which is safe while Luna is writing to it, and the copy is checked. Other files, and a
  # .db that is not SQLite, are copied as they are.
  local from="$1" to="$2" result=0
  "$PY" - "$from" "$to" <<'PYEOF' || result=$?
import os, sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
with open(src, "rb") as f:
    if f.read(16) != b"SQLite format 3\x00":
        sys.exit(3)
os.umask(0o077)
part = dst + ".part"
if os.path.exists(part):
    os.remove(part)
source, copy = sqlite3.connect(src, timeout=10), sqlite3.connect(part)
try:
    source.backup(copy)
    if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        sys.exit("the copy did not pass SQLite's integrity check")
finally:
    copy.close()
    source.close()
info = os.stat(src)
os.chmod(part, info.st_mode & 0o777)
os.utime(part, ns=(info.st_atime_ns, info.st_mtime_ns))
os.replace(part, dst)
PYEOF
  if [ "$result" -ne 0 ]; then
    rm -f "$to.part"
    if [ "$result" -ne 3 ]; then
      say "Warning: could not copy $from with SQLite; copying the file as it is."
    fi
    cp -p "$from" "$to"
  fi
}

restore_data() {
  # restore_data BACKUP TO, with Luna stopped. Old SQLite journal files would belong to the
  # file being replaced, so they are removed; the copy is moved into place in one step.
  local from="$1" to="$2"
  mkdir -p "$(dirname "$to")"
  rm -f "$to-journal" "$to-wal" "$to-shm"
  cp -p "$from" "$to.restore" && mv -f "$to.restore" "$to"
}

stop_service() {
  # Stops luna.service if it is running; returns 1 when it was not running.
  if command -v systemctl >/dev/null 2>&1 && systemctl --user is-active --quiet luna.service 2>/dev/null; then
    systemctl --user stop luna.service
    say "Luna stopped while her data is restored."
    return 0
  fi
  return 1
}

backup_data() {
  # Settings, memories, plugin choices and task history with AI usage (tasks.db, Luna 1.1+);
  # restored only by: rollback-pi.sh --with-data
  local folder="$STATE/backups/$(date +%Y%m%d-%H%M%S)-$(git rev-parse --short HEAD)"
  mkdir -p "$folder"
  local file
  for file in "$HOME/.ai-robot-hud.json" "${LUNA_MEMORY_FILE:-$HOME/.luna-memory.db}" \
              "${LUNA_PLUGINS_FILE:-$HOME/.luna/plugins.json}" \
              "${LUNA_TASKS_FILE:-$HOME/.luna/tasks.db}"; do
    if [ -f "$file" ]; then copy_data "$file" "$folder/$(basename "$file")"; fi
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

wants_agents() {
  # Background tasks with the real helper: LUNA_TASKS=service here or in ~/.luna.env.
  [ "${LUNA_TASKS:-}" = "service" ] ||
    grep -qsE '^[[:space:]]*(export[[:space:]]+)?LUNA_TASKS=["'"'"']?service' "$HOME/.luna.env"
}

install_packages() {
  if [ "${LUNA_PIP:-}" = "skip" ] || [ ! -x .venv/bin/pip ]; then
    return 0
  fi
  # Luna itself without dependencies (pygame comes from apt on the Pi), then the [voice] and
  # [ai] extras listed in pyproject.toml, and [agents] when background tasks are switched on.
  # An extra the checked-out version does not have (agents before 1.1, e.g. on a rollback to
  # v1.0.1) is skipped.
  local extras="voice ai"
  if wants_agents; then
    extras="$extras agents"
  fi
  .venv/bin/pip install --quiet --no-deps -e . &&
    LUNA_EXTRAS="$extras" .venv/bin/python -c 'import os, tomllib; e = tomllib.load(open("pyproject.toml", "rb"))["project"]["optional-dependencies"]; print("\n".join(p for x in os.environ["LUNA_EXTRAS"].split() for p in e.get(x, [])))' |
    xargs .venv/bin/pip install --quiet
}

check_start() {
  # The version, then one headless frame with mock hardware (no screen, microphone or AI).
  local folder
  folder="$(mktemp -d)"
  if "$PY" -m luna_core --version &&
    SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy LUNA_LOG_FILE=off \
      "$PY" -m luna_core --headless --mock-hardware --screenshot "$folder/luna-check.png" \
      >"$folder/output.txt" 2>&1 &&
    [ -s "$folder/luna-check.png" ]; then
    rm -rf "$folder"
    return 0
  fi
  tail -n 15 "$folder/output.txt" 2>/dev/null || true
  rm -rf "$folder"
  return 1
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
