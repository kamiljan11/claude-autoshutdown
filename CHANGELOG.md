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
- Linux HiDPI follow-up (department review): the 8 s wait for `Xft.dpi` now happens only on a
  GNOME Wayland session and stops at once when `xrdb` is missing; the `monitors.xml` fallback
  prefers the primary monitor over old layouts; the chosen scale and its source go to the log;
  the wait loop is covered by tests with a fake clock; the installer warns when `xrdb` is absent.
- Tiny window on Linux HiDPI (200% scale under XWayland): Tk now reads `Xft.dpi` from
  `xrdb` and scales itself (`linux_screen_scale`, `test_hidpi.py`). At autostart, before GNOME
  publishes `Xft.dpi`, it waits up to 8 s and then falls back to `~/.config/monitors.xml`.
- A native-CLI session on Linux (`~/.local/share/claude/versions/<ver>`) would have been
  treated as dead: liveness now looks for "claude" in the full executable path.
- Arming (button and `arm_on_start`) is refused when "Require user to be idle" is on but idle
  time cannot be measured (non-GNOME Linux without `xprintidle`); `arm_on_start` now also
  checks the action's permission, which it previously skipped.
- **Unknown user idle now blocks the action** (was: "does not block") while "Require user to be
  idle" is on, on both systems; the block lifts when the measurement returns.
- Saving riskier settings while armed (dry run -> live, another action, force-close on, idle
  requirement off, zero sessions allowed) asks for the arming confirmation again; "No" disarms.
- Saving settings while armed re-checks the arming conditions and disarms with a message when
  they no longer hold (closes "untick idle, arm, tick it back" and switching to a forbidden
  action while armed). CI workflow token is now read-only (`permissions: contents: read`).
- Linux instance lock stores the boot id: a lock left by a shutdown no longer blocks the next
  start when PID and boot-relative start time happen to repeat.
- `install-linux.sh` rejects paths that would break the `.desktop` `Exec=` line and escapes
  `%`; CI now really runs the installer in a sandboxed `HOME` and validates every shortcut.
- UI language detection returned `c` under the `C` / `POSIX` locale (CI, systemd services);
  on Linux it now reads `LANGUAGE` / `LC_ALL` / `LC_MESSAGES` / `LANG` and falls back to English.

### Added
- `docs/ARCHITECTURE.md` — module map, where session truth comes from, the decision flow and
  the boundaries of the Windows-only layer.
- `docs/adr/0001` — why turn state is decided from an allow-list of record types.
- `docs/adr/0002` — why a session counts as live only when PID and process start time match.
- `.github/pull_request_template.md`.

Documentation only; no behaviour change.
