# ADR 0003 — Linux as a second backend behind one probe contract

- Status: accepted
- Date: 2026-09-26

## Context

The tool was Windows-only: every OS call lived in `winprobe.py`, but `monitor.py` also called
`tasklist` directly and `autoshutdown.py` hard-coded the FILETIME tolerance (`10_000_000`) for
its instance lock. The same user now runs Claude Code on Ubuntu 26.04 (GNOME, Wayland) and
wants the same "off when the work is done" behaviour there.

Measured on that machine before writing code (Claude Code 2.1.280):

- `~/.claude/sessions/<PID>.json` has the same shape; `procStart` is field 22 of
  `/proc/<pid>/stat` — clock ticks since boot (`SC_CLK_TCK` = 100), not FILETIME.
  Two live sessions: `633076 == 633076`, `641659 == 641659`.
- The desktop-app session binary is `~/.config/Claude/claude-code/<ver>/claude`; the native CLI
  is `~/.local/share/claude/versions/<ver>` — a file named `2.1.283`, without "claude" in it.
- logind answers `CanPowerOff = yes`, `CanSuspend = yes`, `CanHibernate = no`.
  `loginctl lock-sessions` is `auth_admin_keep` in polkit; locking one's own session is not.
- GNOME Mutter's `IdleMonitor.GetIdletime` over D-Bus works under Wayland.

## Decision

- `probe.py` selects `winprobe` or `linuxprobe` at import time. `monitor.py` and
  `autoshutdown.py` import only `probe`. Both backends expose the same names:
  `ProcInfo`, `probe_process`, `process_names`, `claude_code_pids`, `human_idle_seconds`,
  `POWER_ACTIONS`, `power_action`, `shutdown_capability(action)`, `available_sleep_states`,
  `FILETIME_PER_SECOND`, `PROC_START_UNITS_PER_SECOND`.
- `ProcInfo` keeps its field names. `created_filetime` holds whatever unit Claude Code writes
  to `procStart` on that OS; `PROC_START_UNITS_PER_SECOND` tells the engine what one second is.
  `cpu_100ns` is converted to 100 ns on Linux so the CPU maths is identical.
- Session liveness checks "claude" in the full executable path, not the file name.
- Power actions go through logind with no `sudo`: `systemctl poweroff -i` (`--check-inhibitors=no`)
  (the Linux counterpart of `/f`), `systemctl hibernate`, `systemctl suspend`,
  `loginctl lock-session auto`. Capability is asked per action (`Can*`); `challenge` counts as
  a refusal, because nobody types a polkit password at 3 AM.

## Consequences

- The engine, UI, config, safety gates and the 13-stage end-to-end test are shared; the
  end-to-end test passes on Linux unchanged apart from its import.
- Renaming `created_filetime` / `cpu_100ns` would be cleaner but touches every test; the unit
  is documented at the top of `linuxprobe.py` instead.
- The liveness check is slightly looser on Windows (path instead of name). The start-time
  match still has to pass, so a recycled PID is still rejected.
- Idle time on non-GNOME desktops needs `xprintidle`; without it idle is unknown. The engine
  keeps treating unknown idle as "does not block" (a machine must not stay on forever because
  of a measurement gap), so the decision moves to arming: `arm_refusal()` refuses to arm —
  from the button and from `arm_on_start` — while "Require user to be idle" is on and idle
  cannot be measured. The user either fixes the measurement or unticks the option knowingly
  (pg-review 2026-10-02, security-1).
- Process start time on Linux is relative to boot, so the instance lock also stores
  `/proc/sys/kernel/random/boot_id`; a lock from a previous boot is ignored even if PID and
  start ticks happen to match (pg-review 2026-10-02, data-1).
- macOS is out of scope: no logind / systemd, and no session registry measurements.
