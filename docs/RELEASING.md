# Releasing, installing and rolling back Luna Core

Versions are git tags `vX.Y.Z` (see `CHANGELOG.md`). A release is tagged **only after the
owner approves it**.

## 1. Before a release
1. All tests pass on Windows:
   `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`
2. The CI run for the release branch is green (Linux, Windows, ARM Linux, Python 3.11-3.13,
   and the slow job: clean install + upgrade/failed upgrade/rollback):
   `gh run list --branch release/X.Y.Z`
3. `luna_core.__version__` and `pyproject.toml` have the new version; `CHANGELOG.md` lists the changes.
4. Try it on the Pi (section 3) with the release branch: `bash scripts/upgrade-pi.sh release/X.Y.Z`.

## 2. After approval
```bash
git checkout main && git merge --ff-only release/X.Y.Z
git tag -a vX.Y.Z -m "Luna Core X.Y.Z" && git push origin main vX.Y.Z
```
Change the CHANGELOG heading from "unreleased" to the release date in the same merge.
Apps then pin the version: `luna-core @ git+https://github.com/<you>/ai-robot@vX.Y.Z`.

## 3. On the Raspberry Pi
| Task | Command |
| --- | --- |
| First install | `bash scripts/install-pi.sh` |
| Start with the desktop | `bash scripts/install-service.sh` (`--remove` to undo) |
| Upgrade | `bash scripts/upgrade-pi.sh vX.Y.Z` (or `main`) |
| Undo the last upgrade | `bash scripts/rollback-pi.sh` (`--with-data`: also settings and memories) |
| Logs | `journalctl --user -u luna` and `~/.luna/logs/luna.log` |
| Stop / start | `systemctl --user stop luna` / `systemctl --user start luna` |

**Upgrade** (`upgrade-pi.sh`): refuses if there are local code changes, backs up
`~/.ai-robot-hud.json`, `~/.luna-memory.db` and `~/.luna/plugins.json` to
`~/.luna/backups/<time>-<commit>`, checks out the version, installs the packages, and checks
that Luna starts (version + one headless frame with mock hardware). If that fails it checks out
the earlier commit again and exits with an error; otherwise it restarts the service and
remembers the earlier commit in `~/.luna/previous-version`.

**Rollback** (`rollback-pi.sh`): checks out `previous-version` (running it again undoes the
rollback). Settings and memories are kept unless `--with-data` is given, because a newer version
may have saved things the user wants. Backups are never deleted automatically.

**Service** (`install-service.sh`): a systemd *user* service, `~/.config/systemd/user/luna.service`,
started by `~/.config/autostart/luna.desktop` when the desktop logs in (so Luna gets the screen).
It restarts Luna 3 s after a crash, at most 5 times in 2 minutes; Quit in Luna stops it for that
session. For a robot that starts by itself, turn on desktop auto-login (`sudo raspi-config` >
System Options > Boot / Auto Login).

**With Robot Setup**: [robot-setup](https://github.com/darshanadinushal/robot-setup) uses the
same `luna.service` name. Only one of them owns Luna's startup at a time:
- Robot Setup (0.4.0 or later) backs up and replaces the files written by `install-service.sh`
  for the same Luna folder, and puts them back on `robot-setup remove`.
- `install-service.sh` (and `--remove`) refuses to touch Robot Setup's files; use
  `robot-setup repair` or `robot-setup remove` there.
- `robot-setup upgrade-luna vX.Y.Z` and `robot-setup rollback-luna` run `upgrade-pi.sh` and
  `rollback-pi.sh` above; `restart_service` restarts whichever `luna.service` is running.

**Performance on the Pi**: measure with `scripts/measure_startup.py` (start to first frame, peak
memory, idle CPU and memory) and `scripts/measure_listening.py`; record results in
`docs/BASELINE.md`. `run-pi.sh` sets `MALLOC_ARENA_MAX=2` (set it yourself to change it).

Environment for the scripts: `LUNA_PYTHON` (Python to use), `LUNA_PIP=skip` (do not reinstall
packages), `LUNA_STATE_DIR` (default `~/.luna`, for versions and backups).
