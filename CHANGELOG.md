# Changelog

Notable changes to Claude AutoShutdown. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this file starts at the point it was introduced, so earlier history lives in the git log.

## [Unreleased]

### Added
- `docs/ARCHITECTURE.md` — module map, where session truth comes from, the decision flow and
  the boundaries of the Windows-only layer.
- `docs/adr/0001` — why turn state is decided from an allow-list of record types.
- `docs/adr/0002` — why a session counts as live only when PID and process start time match.
- `.github/pull_request_template.md`.

Documentation only; no behaviour change.
