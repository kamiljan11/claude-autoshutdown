# Changelog

Notable changes to Claude AutoShutdown. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this file starts at the point it was introduced, so earlier history lives in the git log.

## [Unreleased]

### Added
- **Linux support** (systemd; verified on Ubuntu 26.04 GNOME Wayland): `linuxprobe.py` backend
  (`/proc`, logind power actions without `sudo`, idle time from GNOME Mutter or `xprintidle`),
  `probe.py` backend selector, `install-linux.sh` (app-menu + desktop shortcut, optional
  autostart), `assets/icon.png`, `test_linuxprobe.py`, `ubuntu-latest` CI job.
- `docs/adr/0003` — why Linux is a second backend behind one contract.

### Changed
- `monitor.py` no longer calls `tasklist` itself; process listing moved into the backends.
- Arming checks the permission for the chosen action, not always for shutdown.

### Fixed
- A native-CLI session on Linux (`~/.local/share/claude/versions/<ver>`) would have been
  treated as dead: liveness now looks for "claude" in the full executable path.
- UI language detection returned `c` under the `C` / `POSIX` locale (CI, systemd services);
  on Linux it now reads `LANGUAGE` / `LC_ALL` / `LC_MESSAGES` / `LANG` and falls back to English.

### Added
- `docs/ARCHITECTURE.md` — module map, where session truth comes from, the decision flow and
  the boundaries of the Windows-only layer.
- `docs/adr/0001` — why turn state is decided from an allow-list of record types.
- `docs/adr/0002` — why a session counts as live only when PID and process start time match.
- `.github/pull_request_template.md`.

Documentation only; no behaviour change.
