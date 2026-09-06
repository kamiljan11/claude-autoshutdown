# Glossary

The words this program uses to decide whether your machine may sleep. Most bugs in a tool like
this come from two people meaning different things by "idle".

| Term | Meaning here |
|---|---|
| **session** | One Claude Code / Cowork process, identified by a file in `~/.claude/sessions/<PID>.json`. It counts as live only if the PID is alive *and* the process start time matches the one recorded in that file (see ADR 0002). |
| **subagent** | A parallel agent spawned by a session; writes its own transcript under `<sessionId>/subagents/*.jsonl`. A session whose own turn looks closed is still **working** if a subagent wrote within `SUBAGENT_ACTIVE_WINDOW` (120 s). |
| **turn** | One exchange in the conversation. `CLOSED` — the model answered and is waiting for a human. `OPEN` — something is still running (a tool, thinking, a compaction). `UNKNOWN` — no transcript, or it cannot be read. |
| **decisive record** | A transcript record whose type is in `DECISIVE_RECORD_TYPES` (`assistant`, `user`). Everything else is ignored when deciding the turn state — deliberately an allow-list (ADR 0001). |
| **idle (session)** | Turn closed, no subagent activity in the window, CPU below the threshold. Not the same as "no output on screen". |
| **human idle** | Seconds since the last keyboard or mouse input, from Win32 `GetLastInputInfo`. With `require_human_idle` on, a person at the keyboard blocks the action even when every session is idle. |
| **quiet period** | `quiet_seconds` of continuous idleness, confirmed `required_polls` times in a row. One quiet poll is never enough — a model thinking between tool calls looks idle for a moment. |
| **guard process** | A process name from `guard_patterns` (a render, a backup, a long build). Present → no power action, regardless of session state. Your "do not shut down while this runs" list. |
| **countdown** | The `countdown_seconds` window in which the on-screen dialog can still cancel the action. The last chance for a human to say no. |
| **action** | What happens when everything above agrees: `shutdown`, `hibernate`, `sleep` or `lock`. |
| **dry run** | `dry_run: true` — the full decision path runs and is logged, the power action is not performed. The recommended way to spend the first night with this tool. |
| **stale entry** | A session file left behind by a dead process. Ignored, because Windows recycles PIDs and a stale file would otherwise keep the machine awake forever. |
