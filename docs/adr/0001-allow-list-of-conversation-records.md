# ADR 0001 — Decide turn state from an allow-list of record types, not a noise list

- Status: accepted
- Date: 2026-09-06 (documenting a decision already implemented in `monitor.py`)

## Context

Whether a session is still working is read from its transcript (`<sessionId>.jsonl`). The last
records tell us whether the model has answered and is waiting for a human (`CLOSED`) or
something is still in flight — a tool call, thinking, a compaction (`OPEN`).

The obvious implementation is a deny-list: "ignore the technical noise, look at the rest".
Live transcripts on this machine already contain record types that did not exist when this
program was written: `frame-link`, `pr-link`, `artifact-comment-monitor`, `permission-mode`
and others. Anthropic adds more over time, without notice.

## Decision

`DECISIVE_RECORD_TYPES = {"assistant", "user"}` — an **allow-list**. Every other record type is
ignored when deciding the turn state.

## Consequences

- A record type introduced next month is ignored, and the previously detected turn state stands.
- With a deny-list the same new type would fall through as "unknown", wipe out a detected open
  turn, and the machine could shut down in the middle of a run. That failure is silent and
  expensive; the allow-list failure mode (ignoring a genuinely meaningful new type) is not, and
  has not occurred.
- `TURN_UNKNOWN` is kept for the case that actually deserves it: no transcript, or a file that
  cannot be read.
