# Audit status — medium / low findings

Source: [`audit-findings.json`](audit-findings.json) (89 confirmed; all 8 critical and 24 high
were fixed before 1.0). This file tracks the remaining 57 medium/low after the 2026-09-06 pass.

Legend: **fixed** = code + test in this repo · **obsolete** = the code it pointed at no longer
exists (fixed as a side effect of an earlier change) · **dup** = same defect as another entry ·
**out of scope** = deliberately not done, with the reason.

| # | id | status | where / why |
|---|---|---|---|
| 1 | no-transcript-max-shutdown-eligible | obsolete | UNKNOWN turn now blocks; `started_at` always bounds silence |
| 2 | no-disarm-after-action | fixed | `execute_action` disarms after any executed action |
| 3 | guard-scan-fails-open | fixed | `matching_guard_processes` → `None` on failure; `evaluate` blocks on `None` |
| 4 | invalid-action-crashes-startup | fixed | `validate_config`: unknown action → `nothing` + warning |
| 5 | tasklist-fail-otwiera-bramke-straznikow | dup | = 3 |
| 6 | autoarm-plus-dziedziczony-tryb-bojowy | obsolete | comment corrected earlier; behaviour documented |
| 7 | jeden-katalog-konfiguracji | out of scope | WSL / second `CLAUDE_CONFIG_DIR` — documented in README limitations |
| 8 | zmiana-ustawien-w-trakcie-odliczania | fixed | `save_settings` cancels a running countdown |
| 9 | odliczanie-zero-sekund-zawiesza-bramke | fixed | `CountdownWindow.start()` runs only after `app.countdown` is set; min 5 s |
| 10 | watek-monitora-umiera-poza-try | obsolete | fixed earlier (poll_seconds coercion inside try) |
| 11 | countdown-action-i-dryrun-czytane-dopiero-przy-odpaleniu | fixed | action + dry_run frozen at countdown start |
| 12 | watek-monitora-umiera-poza-try | dup | = 10 |
| 13 | mtime-w-przyszlosci-zamraza-cisze-na-zero | fixed | future mtime → scanner warning in log (blocking stays, but is explained) |
| 14 | cpu-threshold-zero-blokuje-na-zawsze | obsolete | CPU removed from the decision entirely |
| 15 | config-countdown-zero-brak-szansy-na-anulowanie | dup | = 9 |
| 16 | guard-patterns-string-rozpada-sie-na-litery | fixed | string → split on commas; non-list → ignored + warning |
| 17 | countdown-tryb-zmienny-w-locie | dup | = 11 |
| 18 | allow-zero-sessions-string-jest-prawda | fixed | `_coerce_bool`: `"false"` / `"no"` / `"0"` are false |
| 19 | shutdown-capability-czysto-kosmetyczna | obsolete | arming refuses when privilege missing (done before 1.0) |
| 20 | shutdown-bez-force-czeka-na-spiacego-czlowieka | obsolete | `/f` added before 1.0 |
| 21 | stop-file-rozszerzenie-txt | fixed | `stop_file_present()` accepts `STOP`, `STOP.txt`, `stop.txt` |
| 22 | akcja-zgloszona-jako-wykonana-bez-weryfikacji | obsolete | exit code checked before 1.0 |
| 23 | brak-preflight-wykonalnosci-akcji | dup | = 19 |
| 24 | scan-bez-izolacji-bledu-per-sesja | fixed | per-file `try` catches `OSError` + `ValueError`; non-dict skipped |
| 25 | power-action-timeout-zabija-dziecko-i-melduje-sukces | fixed | timeout → failure (`power.timeout`) |
| 26 | odliczanie-dzwoni-co-sekunde-i-restartuje-sie-bez-limitu | fixed | bell at start + last 5 s; 60 s cooldown after cancel |
| 27 | countdown-restart-thrash | dup | = 26 |
| 28 | render-tree-nadpisuje-wybor-w-podgladzie | fixed | `_restoring_selection` flag silences the select handler |
| 29 | podglad-zamarza-po-zniknieciu-sesji | fixed | status shows "session has ended" |
| 30 | render-tree-clobbers-wybor-podgladu | dup | = 28 |
| 31 | tail-events-pusty-panel-przy-duzym-rekordzie | fixed | `tail_events` grows its window like `turn_state` |
| 32 | podglad-slepy-na-subagentow | fixed | Preview lists each subagent transcript under its session (newest first, max 20) |
| 33 | brak-globalnego-lapacza-wyjatkow-cicha-smierc-pod-pythonw | fixed | `sys.excepthook` → `crash.log` + dialog |
| 34 | load-config-nie-utf8-wysadza-start | fixed | `load_config` catches `ValueError` (covers `UnicodeDecodeError`) |
| 35 | subprocess-text-true-strict-decode | fixed | `encoding="utf-8", errors="replace"` on tasklist / powercfg / shutdown |
| 36 | watchdog-zamarcia-bez-oslony-ginie-razem-z-monitorem | fixed | `_check_monitor_alive` wrapped, always reschedules |
| 37 | brak-globalnego-przechwytu-awarii | dup | = 33 |
| 38 | okno-odliczania-obiecuje-nieprawde | fixed | note text depends on `require_human_idle` |
| 39 | config-uszkodzony-po-cichu-resetuje-straznikow-i-akcje | fixed | atomic save + warnings logged |
| 40 | config-nie-slownik-wysadza-start | fixed | non-dict JSON → defaults + warning |
| 41 | powercfg-parse-continue-przed-break | fixed | "not available" header checked before the `continue` |
| 42 | powercfg-parser-pokazuje-niedostepne-stany | dup | = 41 |
| 43 | sleep-rundll32-gubi-argumenty | fixed | `SetSuspendState(0,0,0)` via `ctypes` in a thread |
| 44 | powercfg-parser-listuje-niedostepne-stany | dup | = 41 |
| 45 | arm-dialog-obiecuje-nieegzekwowany-prog-cpu | obsolete | CPU line removed from the arm summary earlier |
| 46 | on-close-porzuca-odliczanie-bez-sladu-w-logu | fixed | `on_close` cancels the countdown with a logged reason |
| 47 | log-rosnie-bez-limitu-przy-powtarzalnym-bledzie-skanera | fixed | scanner warnings logged on change only |
| 48 | tasklist-500ms-co-poll-nawet-rozbrojony | fixed | process scans every 60 s when disarmed, every poll when armed |
| 49 | podglad-przebudowa-co-3s-kasuje-przewijanie | fixed | rebuild only when transcript mtime changed; autoscroll only at bottom |
| 50 | podglad-zapchany-placeholderami-attachment | fixed | `tail_events` returns conversation records only |
| 51 | why-porownuje-cpu-z-1-procentem-nie-z-progiem | obsolete | CPU no longer in `why` |
| 52 | podglad-autoscroll-wyrywa-do-konca | dup | = 49 |
| 53 | powtarzalny-blad-skanera-zalewa-log | dup | = 47 |
| 54 | geometria-okna-skalowana-dpi-bez-przyciecia-do-ekranu | fixed | initial size clamped to 95 % / 90 % of the screen |
| 55 | brak-weryfikacji-zapisywalnosci-katalogu-stanu | fixed | `state_dir_writable()` checked at startup |
| 56 | brak-testu-check-otwartej-tury-w-izolacji | fixed | `test_otwarta_tura_blokuje_niezaleznie_od_working` |
| 57 | brak-testu-galezi-zero-sesji-po-widzianych | fixed | `test_sesje_zniknely_po_tym_jak_byly_wymaga_ciszy` |

Totals: **44 fixed · 8 obsolete · 5 dup · 1 out of scope** (7, WSL / second config dir, is the only deliberate omission).
