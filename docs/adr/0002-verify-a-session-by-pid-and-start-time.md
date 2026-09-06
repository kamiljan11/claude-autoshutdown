# ADR 0002 — A session counts as live only when PID *and* process start time match

- Status: accepted
- Date: 2026-09-06 (documenting a decision already implemented in `monitor.py`)

## Context

`~/.claude/sessions/<PID>.json` is written when a session starts and is **not** removed when the
process dies. The directory therefore mixes live sessions with leftovers.

Windows recycles PIDs. A leftover file for PID 12345 plus any unrelated process that later
receives PID 12345 makes a dead session look alive — and the program then never shuts the
machine down, which is exactly the bug the tool exists to prevent.

## Decision

Every registry entry is verified twice before it counts:

1. the PID is alive (`winprobe.probe_process`), and
2. the process start time equals `procStart` from the file, within `PROC_START_TOLERANCE`
   (one second in FILETIME units).

An entry that fails either check is treated as stale and ignored.

## Consequences

- Recycled PIDs cannot resurrect a dead session.
- The comparison needs a tolerance: the registry stores FILETIME written by another process, so
  an exact match is not guaranteed. One second is far below the interval at which a PID can
  realistically be recycled into a *different* process on a desktop machine.
- `claude_processes_without_registry()` covers the mirror case — a Claude process running with
  no registry entry — so a missing file cannot silently authorise a shutdown either.
