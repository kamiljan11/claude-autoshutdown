# Changelog

Notable changes to Claude AutoShutdown. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this file starts at the point it was introduced, so earlier history lives in the git log.

## [Unreleased]

### Added
- Stalled-session warning. An open turn with no live subagents and no transcript write for
  30 minutes now shows a red line in the header, colours the row, rings the bell once and
  writes one log entry on entry and on exit. Prompted by a real session that held the machine
  on for 9h53m because it was waiting for a permission prompt to be approved.
- 14th end-to-end stage reproducing that session: 10 hours of silence must produce both a held
  gate and the warning.

### Unchanged (deliberately)
- The shutdown gate. A stalled session still counts as working, because its work really is
  half-finished. `test_stalled_does_not_change_the_shutdown_gate` pins this.

- `docs/ARCHITECTURE.md` — module map, where session truth comes from, the decision flow and
  the boundaries of the Windows-only layer.
- `docs/adr/0001` — why turn state is decided from an allow-list of record types.
- `docs/adr/0002` — why a session counts as live only when PID and process start time match.
- `.github/pull_request_template.md`.
