"""Teksty interfejsu w dwoch jezykach. Domyslnie angielski.

Uzycie: t("key", **params). Brak klucza w wybranym jezyku -> angielski -> sam klucz,
zeby literowka w kluczu byla widoczna na ekranie, a nie cichym pustym napisem.
"""

from __future__ import annotations

import locale
import sys

LANGUAGES: dict[str, str] = {"en": "English", "pl": "Polski"}
DEFAULT_LANGUAGE = "en"
AUTO = "auto"  # wartosc w config.json: "wez jezyk systemu, jesli go mamy"

_current = DEFAULT_LANGUAGE


def system_language() -> str:
    """Dwuliterowy kod jezyka interfejsu Windows (np. 'en', 'pl'), 'en' gdy nieznany.

    Na Windows pytamy o jezyk UI uzytkownika, nie o locale procesu Pythona - to
    pierwsze jest tym, co uzytkownik faktycznie widzi w systemie.
    """
    name = ""
    if sys.platform == "win32":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85):
                name = buf.value  # np. "en-GB", "pl-PL"
        except (AttributeError, OSError):
            name = ""
    if not name:
        try:
            name = locale.getlocale()[0] or ""  # np. "pl_PL", "English_United Kingdom"
        except (ValueError, TypeError):
            name = ""
    code = name.replace("_", "-").split("-")[0].lower()
    aliases = {"english": "en", "polish": "pl"}
    return aliases.get(code, code[:2]) if code else DEFAULT_LANGUAGE


def resolve_language(setting: str | None) -> str:
    """'auto' -> jezyk systemu (jesli obslugiwany), inaczej angielski; kod -> kod."""
    if not setting or setting == AUTO:
        candidate = system_language()
    else:
        candidate = setting
    return candidate if candidate in LANGUAGES else DEFAULT_LANGUAGE


def set_language(code: str | None) -> str:
    """Ustawia jezyk ('auto' dozwolone). Zwraca to, co realnie ustawiono."""
    global _current
    _current = resolve_language(code)
    return _current


def current_language() -> str:
    return _current


def t(key: str, **params: object) -> str:
    table = STRINGS.get(_current) or STRINGS[DEFAULT_LANGUAGE]
    text = table.get(key) or STRINGS[DEFAULT_LANGUAGE].get(key) or key
    try:
        return text.format(**params) if params else text
    except (KeyError, IndexError):
        return text


STRINGS: dict[str, dict[str, str]] = {
    "en": {
        # --- session state ------------------------------------------------
        "state.working": "WORKING",
        "state.idle": "IDLE",
        "surface.cowork": "Cowork",
        "surface.cli": "CLI",
        # --- turn reasons (monitor.classify_turn / turn_state) -------------
        "turn.compacting": "compacting context",
        "turn.tool_in_flight": "tool call in flight",
        "turn.waiting_for_you": "waiting for you",
        "turn.cut_at_token_limit": "cut off at token limit",
        "turn.reply_in_progress": "reply in progress ({stop})",
        "turn.processing_tool_result": "model is processing a tool result",
        "turn.model_thinking": "model is thinking / compacting",
        "turn.unknown_record": "unknown record ({kind})",
        "turn.no_transcript": "no transcript",
        "turn.cannot_read": "cannot read ({error})",
        "turn.no_conversation_record": "no conversation record in transcript",
        # --- why a session counts as working --------------------------------
        "why.blocking_since": "{reason} - blocking for {duration}",
        "why.subagents_writing": "{n} subagent(s) writing",
        "why.unknown_turn": "unknown turn state: {reason}",
        "why.fresh_write": "recent transcript write",
        "why.silence": "quiet",
        # --- shutdown conditions (monitor.evaluate) ---------------------------
        "check.armed": "Armed",
        "check.armed.yes": "yes",
        "check.armed.no": "click ARM",
        "check.no_stop_file": "No STOP file",
        "check.no_stop_file.blocked": "STOP file blocks the action",
        "check.no_stop_file.ok": "none",
        "check.scanner_ok": "Scanner healthy",
        "check.scanner_ok.ok": "clean",
        "check.registry": "Every Claude session visible in registry",
        "check.registry.stray": "processes without an entry: {pids}",
        "check.registry.ok": "matches",
        "check.all_idle": "All sessions idle",
        "check.all_idle.ok": "{n} session(s) at rest",
        "check.turns_closed": "Every turn closed",
        "check.turns_closed.ok": "{n} turn(s) closed (end_turn)",
        "check.quiet_each": "Quiet for at least {s}s in every session",
        "check.quiet_each.detail": "shortest quiet {d}",
        "check.quiet_since_last": "Quiet for at least {s}s since the last session",
        "check.quiet_since_last.none": "no sessions since program start",
        "check.quiet_since_last.gone": "last session disappeared {d} ago",
        "check.human_idle": "User idle for at least {s}s",
        "check.human_idle.unknown": "unknown",
        "check.human_idle.detail": "last input {d} ago",
        "check.no_guards": "No guard processes running",
        "check.no_guards.ok": "clean",
        "check.confirmed": "Confirmed {n}x in a row",
        # --- power actions ----------------------------------------------------
        "action.shutdown": "Shut down the computer",
        "action.hibernate": "Hibernate",
        "action.sleep": "Sleep (usually hibernates when hibernation is enabled)",
        "action.lock": "Lock the screen",
        "action.nothing": "Do nothing (log only)",
        "power.nothing_done": "{label} - nothing executed",
        "power.skipped_non_windows": "{label} - skipped (not Windows)",
        "power.sent": "{label} - command sent: {cmd}",
        "power.slow": "{label} - command taking longer than usual: {cmd}",
        "power.cannot_start": "{label} - could not start: {error}",
        "power.done": "{label} - executed ({cmd})",
        "power.failed": "{label} - FAILED, exit code {code}: {reason} | command: {cmd}",
        "power.unknown_action": "unknown action: {name}",
        "priv.enabled": "SeShutdownPrivilege enabled - shutdown available",
        "priv.no_token": "cannot open the process token",
        "priv.missing": "SeShutdownPrivilege not present on this system",
        "priv.not_assigned": "account lacks the shutdown privilege (ERROR_NOT_ALL_ASSIGNED)",
        "priv.adjust_error": "AdjustTokenPrivileges error {code}",
        "priv.non_windows": "not Windows",
        # --- header ---------------------------------------------------------------
        "app.title": "Claude AutoShutdown",
        "header.armed": "● ARMED",
        "header.disarmed": "● DISARMED",
        "header.monitor_silent": "● MONITOR SILENT",
        "header.monitor_dead": "● MONITOR DEAD",
        "header.starting": "Starting scanner...",
        "header.stale": "No fresh data for {d} - do not trust this view",
        "header.dead": "Scanner thread is dead - restart the program",
        "header.counts": "sessions: {n}  ·  working: {w}{mode}",
        "header.mode_dry": " · dry run",
        "header.mode_live": " · LIVE MODE",
        "header.conditions_met": "Conditions met - counting down",
        "header.waiting": "Waiting - {reason}",
        "header.more": "  (+{n} more)",
        "btn.arm": "ARM",
        "btn.disarm": "DISARM",
        "btn.lang": "PL",
        # --- navigation -------------------------------------------------------------
        "nav.monitor": "Monitor",
        "nav.preview": "Preview",
        "nav.settings": "Settings",
        "nav.log": "Log",
        "nav.stop_hint": "STOP = a file in the program\nfolder blocks the action",
        # --- monitor page ---------------------------------------------------------------
        "monitor.title": "Live Claude sessions",
        "col.state": "State",
        "col.name": "Session",
        "col.where": "Folder / type",
        "col.why": "Why",
        "col.silence": "Quiet",
        "col.cpu": "CPU",
        "col.subs": "Subagents",
        "col.pid": "PID",
        "monitor.subs_active": "{n} active",
        "monitor.conditions": "Shutdown conditions (all must be green)",
        "monitor.ok": "OK",
        "monitor.no": "NO",
        # --- preview page -------------------------------------------------------------------
        "preview.session": "Session:",
        "preview.no_transcript": "No transcript for this session.",
        "preview.status": "{state} · last write {d} ago · {file}",
        "preview.who.assistant": "Claude",
        "preview.who.user": "You / tool",
        "preview.who.system": "system",
        "preview.who.subagent": "subagent/{who}",
        "preview.thinking": "[thinking]",
        "preview.result": "<- result: {body}",
        # --- settings page ------------------------------------------------------------------
        "settings.quiet": "Session quiet time [s]",
        "settings.quiet.hint": "seconds without a transcript write = session finished",
        "settings.poll": "Scan interval [s]",
        "settings.poll.hint": "how often to scan",
        "settings.polls": "Confirmations in a row",
        "settings.polls.hint": "this many consecutive clean cycles required",
        "settings.countdown": "Countdown [s]",
        "settings.countdown.hint": "time to cancel",
        "settings.human_idle": "User idle time [s]",
        "settings.human_idle.hint": "how long without mouse/keyboard before shutdown is allowed",
        "settings.action": "Action",
        "settings.dry_run": "Dry run (never shuts down, only logs)",
        "settings.dry_run.hint": "untick when you trust it",
        "settings.require_idle": "Require user to be idle",
        "settings.require_idle.hint": "protects you while you sit at the computer",
        "settings.zero_sessions": "Allow acting with zero sessions since start",
        "settings.zero_sessions.hint": "off by default",
        "settings.arm_on_start": "Arm automatically at start",
        "settings.arm_on_start.hint": "no clicking after a reboot - every other gate still applies",
        "settings.force": "Force-close applications (/f)",
        "settings.force.hint": "without it Windows may show a blocking screen and wait for a click",
        "settings.language": "Language",
        "settings.guards": "Guard processes (comma-separated)",
        "settings.guards.hint": "e.g. ffmpeg, handbrake - while they live, nothing shuts down",
        "settings.save": "Save settings",
        "settings.saved": "saved",
        "settings.privileges": "Privileges",
        "settings.priv_ok": "OK  ",
        "settings.priv_missing": "MISSING  ",
        "settings.power_states": "Power states supported by the system: {states}",
        "settings.err_numbers": "Error",
        "settings.err_numbers.body": "Numbers, please. Check the fields.",
        "settings.err_save": "Save error",
        # --- arming ----------------------------------------------------------------------------
        "arm.refused_title": "Cannot arm",
        "arm.refused_body": "The action '{label}' needs the shutdown privilege and the system "
                            "denies it:\n\n{why}\n\nChoose another action in Settings.",
        "arm.mode_dry": "DRY RUN - nothing will happen",
        "arm.mode_live": "WILL REALLY EXECUTE THE ACTION",
        "arm.summary": "Action: {label}\nMode: {mode}\n\nConditions:\n"
                       "  - every session quiet for {quiet} s\n"
                       "  - no working subagents\n"
                       "  - every turn closed (end_turn), none unknown\n"
                       "  - {polls} confirmations in a row\n",
        "arm.summary_idle": "  - no mouse/keyboard input for {idle} s\n",
        "arm.summary_countdown": "  - {countdown} s countdown with a CANCEL button\n\n",
        "arm.summary_force": "Applications will be FORCE-closed (/f): Windows will not ask,\n"
                             "but unsaved work in other programs will be lost.\n\n",
        "arm.summary_question": "Arm?",
        "arm.confirm_title": "Confirm arming",
        # --- countdown ---------------------------------------------------------------------------
        "countdown.title": "Claude AutoShutdown - countdown",
        "countdown.heading": "All sessions have finished",
        "countdown.note": "Mouse movement or new session activity also aborts the action",
        "countdown.cancel": "CANCEL  (Esc)",
        "countdown.now": "Execute now",
        "countdown.reason.button": "CANCEL button",
        "countdown.reason.esc": "Esc key",
        "countdown.reason.close": "countdown window closed",
        "countdown.reason.unknown": "source unknown",
        "countdown.reason.disarm": "disarmed during countdown",
        "countdown.reason.conditions": "conditions no longer met ({blockers})",
        "countdown.reason.settings": "settings changed during countdown",
        "countdown.reason.close_app": "program closed during countdown",
        "countdown.note_no_idle": "New session activity aborts the action (user idle check is off)",
        "check.no_guards.scan_failed": "could not read the process list - blocking to be safe",
        "scan.future_mtime": "{name}: transcript timestamp is in the future (clock skew?)",
        "preview.session_gone": "This session has ended - showing its last known transcript.",
        "preview.subagent_entry": "     ↳ subagent {name} (written {age} ago)",
        "preview.no_events": "No conversation records in the transcript yet.",
        "power.timeout": "{label} - command did not finish within {seconds}s and was killed: {cmd}",
        "log.crash": "CRASH: {error} (full traceback in crash.log)",
        "crash.title": "Claude AutoShutdown crashed",
        "crash.body": "The program hit an unexpected error and is NOT monitoring anymore.\n\n"
                      "Details: {path}",
        "state.not_writable": "Cannot write to the program folder:\n{path}\n\nWithout it there is "
                              "no log, no lock file and no STOP switch. Move the program or set "
                              "CLAUDE_AUTOSHUTDOWN_HOME.",
        # --- dialogs after action ----------------------------------------------------------------------
        "dry.title": "Dry run",
        "dry.body": "Conditions met - in live mode the computer would now get the action: {label}.",
        "action.failed_title": "Action failed",
        "action.failed_body": "{detail}\n\nThe program has disarmed itself. Details in the Log tab.",
        "action.stale_title": "Action withheld",
        "action.stale_body": "The scanner stopped delivering data, so I cannot tell whether "
                             "sessions are still working.\n\nThe program disarmed itself "
                             "instead of guessing.",
        "close.title": "Quit?",
        "close.body": "The program is ARMED. Quitting stops the monitoring. Quit?",
        "instance.title": "Already running",
        "instance.body": "Claude AutoShutdown is already running as process {pid}.\n\n"
                         "Two instances could execute the action twice, so this one exits.",
        # --- log lines ----------------------------------------------------------------------------------
        "log.start": "Program started (DISARMED, {mode}, action: {action}, quiet {quiet}s, "
                     "user idle {idle}s)",
        "log.mode_dry": "dry run",
        "log.mode_live": "LIVE MODE",
        "log.autoarm_env": "AUTOARM: armed without confirmation (CLAUDE_AUTOSHUTDOWN_AUTOARM=1) "
                           "- end-to-end test mode",
        "log.autoarm_cfg": "Armed automatically at start (arm_on_start=true) - action: {action}, "
                           "dry run: {dry}",
        "log.settings_saved": "Settings saved: {cfg}",
        "log.disarmed": "DISARMED (manually)",
        "log.arm_refused": "Arming refused - missing privilege: {why}",
        "log.armed": "ARMED - action={action}, dry_run={dry}",
        "log.scanner_warning": "Scanner warning: {error}",
        "log.monitor_error": "Monitor thread error: {error}",
        "log.gui_error": "GUI loop error: {error}",
        "log.waiting": "Waiting because: {blockers}",
        "log.all_green": "All conditions green",
        "log.countdown_start": "Conditions met {a}/{b} - starting {s} s countdown",
        "log.countdown_cancelled": "Countdown CANCELLED: {reason}",
        "log.skipped_disarmed": "Action skipped - program is disarmed",
        "log.skipped_stop": "Action skipped - STOP file appeared",
        "log.withheld_stale": "Action WITHHELD - scanner data stale ({d}), thread alive: {alive}",
        "log.dry_run": "DRY RUN: this is where '{label}' would run. Untick 'Dry run' in Settings "
                       "to make it real.",
        "log.action_done": "ACTION EXECUTED: {detail}",
        "log.action_failed": "ACTION FAILED: {detail}",
        "log.closing": "Program closed",
        "log.language": "Language switched to {lang}",
    },
    "pl": {
        "state.working": "PRACUJE",
        "state.idle": "BEZCZYNNA",
        "surface.cowork": "Cowork",
        "surface.cli": "CLI",
        "turn.compacting": "kompaktowanie kontekstu",
        "turn.tool_in_flight": "narzedzie w toku",
        "turn.waiting_for_you": "czeka na Ciebie",
        "turn.cut_at_token_limit": "urwane na limicie tokenow",
        "turn.reply_in_progress": "odpowiedz w toku ({stop})",
        "turn.processing_tool_result": "model przetwarza wynik narzedzia",
        "turn.model_thinking": "model liczy odpowiedz / kompaktuje",
        "turn.unknown_record": "nieznany rekord ({kind})",
        "turn.no_transcript": "brak transkryptu",
        "turn.cannot_read": "nie moge odczytac ({error})",
        "turn.no_conversation_record": "brak rekordu rozmowy w transkrypcie",
        "why.blocking_since": "{reason} - blokuje od {duration}",
        "why.subagents_writing": "{n} subagent(ow) pisze",
        "why.unknown_turn": "nieznany stan tury: {reason}",
        "why.fresh_write": "swiezy zapis w transkrypcie",
        "why.silence": "cisza",
        "check.armed": "Uzbrojony",
        "check.armed.yes": "tak",
        "check.armed.no": "kliknij UZBROJ",
        "check.no_stop_file": "Brak pliku STOP",
        "check.no_stop_file.blocked": "plik STOP blokuje akcje",
        "check.no_stop_file.ok": "brak",
        "check.scanner_ok": "Skaner bez bledow",
        "check.scanner_ok.ok": "czysto",
        "check.registry": "Kazda sesja Claude widoczna w rejestrze",
        "check.registry.stray": "procesy bez wpisu: {pids}",
        "check.registry.ok": "zgadza sie",
        "check.all_idle": "Wszystkie sesje bezczynne",
        "check.all_idle.ok": "{n} sesji w spoczynku",
        "check.turns_closed": "Kazda tura domknieta",
        "check.turns_closed.ok": "{n} tur domknietych (end_turn)",
        "check.quiet_each": "Cisza min. {s}s w kazdej sesji",
        "check.quiet_each.detail": "najkrotsza cisza {d}",
        "check.quiet_since_last": "Cisza min. {s}s od ostatniej sesji",
        "check.quiet_since_last.none": "zero sesji od startu programu",
        "check.quiet_since_last.gone": "ostatnia sesja zniknela {d} temu",
        "check.human_idle": "Uzytkownik nieaktywny min. {s}s",
        "check.human_idle.unknown": "nieznane",
        "check.human_idle.detail": "ostatni ruch {d} temu",
        "check.no_guards": "Brak procesow-straznikow",
        "check.no_guards.ok": "czysto",
        "check.confirmed": "Potwierdzone {n}x z rzedu",
        "action.shutdown": "Wylacz komputer",
        "action.hibernate": "Hibernacja",
        "action.sleep": "Uspij (na maszynie z hibernacja zwykle hibernuje)",
        "action.lock": "Zablokuj ekran",
        "action.nothing": "Nic nie rob (tylko log)",
        "power.nothing_done": "{label} - nic nie wykonano",
        "power.skipped_non_windows": "{label} - pominieto (nie-Windows)",
        "power.sent": "{label} - komenda wyslana: {cmd}",
        "power.slow": "{label} - komenda dziala dluzej niz zwykle: {cmd}",
        "power.cannot_start": "{label} - nie udalo sie uruchomic: {error}",
        "power.done": "{label} - wykonane ({cmd})",
        "power.failed": "{label} - NIEPOWODZENIE, kod {code}: {reason} | komenda: {cmd}",
        "power.unknown_action": "nieznana akcja: {name}",
        "priv.enabled": "SeShutdownPrivilege wlaczony - wylaczanie dostepne",
        "priv.no_token": "nie moge otworzyc tokenu procesu",
        "priv.missing": "brak SeShutdownPrivilege w systemie",
        "priv.not_assigned": "konto nie ma przywileju wylaczania (ERROR_NOT_ALL_ASSIGNED)",
        "priv.adjust_error": "AdjustTokenPrivileges blad {code}",
        "priv.non_windows": "nie-Windows",
        "app.title": "Claude AutoShutdown",
        "header.armed": "● UZBROJONY",
        "header.disarmed": "● ROZBROJONY",
        "header.monitor_silent": "● MONITOR MILCZY",
        "header.monitor_dead": "● MONITOR PADL",
        "header.starting": "Uruchamiam skaner...",
        "header.stale": "Brak swiezych danych od {d} - nie ufaj temu widokowi",
        "header.dead": "Watek skanera nie zyje - uruchom program ponownie",
        "header.counts": "sesje: {n}  ·  pracuja: {w}{mode}",
        "header.mode_dry": " · tryb prob",
        "header.mode_live": " · TRYB BOJOWY",
        "header.conditions_met": "Warunki spelnione - odliczanie",
        "header.waiting": "Czekam - {reason}",
        "header.more": "  (+{n} innych)",
        "btn.arm": "UZBROJ",
        "btn.disarm": "ROZBROJ",
        "btn.lang": "EN",
        "nav.monitor": "Monitor",
        "nav.preview": "Podglad",
        "nav.settings": "Ustawienia",
        "nav.log": "Log",
        "nav.stop_hint": "STOP = plik w katalogu\nprogramu blokuje akcje",
        "monitor.title": "Zywe sesje Claude",
        "col.state": "Stan",
        "col.name": "Sesja",
        "col.where": "Katalog / typ",
        "col.why": "Dlaczego",
        "col.silence": "Cisza",
        "col.cpu": "CPU",
        "col.subs": "Subagenci",
        "col.pid": "PID",
        "monitor.subs_active": "{n} aktywnych",
        "monitor.conditions": "Warunki wylaczenia (wszystkie musza byc zielone)",
        "monitor.ok": "OK",
        "monitor.no": "NIE",
        "preview.session": "Sesja:",
        "preview.no_transcript": "Brak transkryptu dla tej sesji.",
        "preview.status": "{state} · ostatni zapis {d} temu · {file}",
        "preview.who.assistant": "Claude",
        "preview.who.user": "Ty / narzedzie",
        "preview.who.system": "system",
        "preview.who.subagent": "subagent/{who}",
        "preview.thinking": "[myslenie]",
        "preview.result": "<- wynik: {body}",
        "settings.quiet": "Cisza sesji [s]",
        "settings.quiet.hint": "ile sekund bez zapisu do transkryptu = sesja skonczyla",
        "settings.poll": "Co ile sprawdzac [s]",
        "settings.poll.hint": "czestotliwosc skanu",
        "settings.polls": "Potwierdzen z rzedu",
        "settings.polls.hint": "tyle cykli pod rzad musi byc czysto",
        "settings.countdown": "Odliczanie [s]",
        "settings.countdown.hint": "czas na anulowanie",
        "settings.human_idle": "Bezczynnosc uzytkownika [s]",
        "settings.human_idle.hint": "ile nie ruszasz myszy zanim wolno wylaczyc",
        "settings.action": "Akcja",
        "settings.dry_run": "Tryb prob (nic nie wylacza, tylko loguje)",
        "settings.dry_run.hint": "zdejmij gdy ufasz",
        "settings.require_idle": "Wymagaj bezczynnosci uzytkownika",
        "settings.require_idle.hint": "chroni gdy siedzisz przy kompie",
        "settings.zero_sessions": "Pozwol dzialac gdy zero sesji od startu",
        "settings.zero_sessions.hint": "domyslnie wylaczone",
        "settings.arm_on_start": "Uzbrajaj sie sam przy starcie",
        "settings.arm_on_start.hint": "zero klikania po restarcie - reszta bramek dziala normalnie",
        "settings.force": "Wymus zamkniecie aplikacji (/f)",
        "settings.force.hint": "bez tego Windows moze pokazac ekran blokujacy i czekac na klikniecie",
        "settings.language": "Jezyk",
        "settings.guards": "Procesy-straznicy (po przecinku)",
        "settings.guards.hint": "np. ffmpeg, handbrake - dopoki zyja, nie wylaczamy",
        "settings.save": "Zapisz ustawienia",
        "settings.saved": "zapisane",
        "settings.privileges": "Uprawnienia",
        "settings.priv_ok": "OK  ",
        "settings.priv_missing": "BRAK  ",
        "settings.power_states": "Stany zasilania wspierane przez system: {states}",
        "settings.err_numbers": "Blad",
        "settings.err_numbers.body": "Liczby, prosze. Sprawdz pola.",
        "settings.err_save": "Blad zapisu",
        "arm.refused_title": "Nie moge uzbroic",
        "arm.refused_body": "Akcja '{label}' wymaga przywileju wylaczania, a system go odmawia:"
                            "\n\n{why}\n\nWybierz inna akcje w Ustawieniach.",
        "arm.mode_dry": "TRYB PROB - nic sie nie stanie",
        "arm.mode_live": "NAPRAWDE WYKONA AKCJE",
        "arm.summary": "Akcja: {label}\nTryb: {mode}\n\nWarunki:\n"
                       "  - kazda sesja cicho przez {quiet} s\n"
                       "  - zero pracujacych subagentow\n"
                       "  - kazda tura domknieta (end_turn), zadna nieznana\n"
                       "  - {polls} potwierdzenia z rzedu\n",
        "arm.summary_idle": "  - brak ruchu myszy przez {idle} s\n",
        "arm.summary_countdown": "  - odliczanie {countdown} s z przyciskiem ANULUJ\n\n",
        "arm.summary_force": "Aplikacje zostana zamkniete WYMUSZONE (/f): Windows nie zapyta "
                             "o zgode,\nale niezapisana praca w innych programach przepadnie.\n\n",
        "arm.summary_question": "Uzbroic?",
        "arm.confirm_title": "Potwierdz uzbrojenie",
        "countdown.title": "Claude AutoShutdown - odliczanie",
        "countdown.heading": "Wszystkie sesje skonczyly prace",
        "countdown.note": "Ruch mysza lub nowa aktywnosc sesji tez przerwie akcje",
        "countdown.cancel": "ANULUJ  (Esc)",
        "countdown.now": "Wykonaj teraz",
        "countdown.reason.button": "przycisk ANULUJ",
        "countdown.reason.esc": "klawisz Esc",
        "countdown.reason.close": "zamkniecie okna odliczania",
        "countdown.reason.unknown": "zrodlo nieznane",
        "countdown.reason.disarm": "rozbrojenie w trakcie odliczania",
        "countdown.reason.conditions": "warunki przestaly byc spelnione ({blockers})",
        "countdown.reason.settings": "zmiana ustawien w trakcie odliczania",
        "countdown.reason.close_app": "zamkniecie programu w trakcie odliczania",
        "countdown.note_no_idle": "Nowa aktywnosc sesji przerwie akcje (bramka bezczynnosci wylaczona)",
        "check.no_guards.scan_failed": "nie moge odczytac listy procesow - blokuje dla bezpieczenstwa",
        "scan.future_mtime": "{name}: czas zapisu transkryptu jest w przyszlosci (zegar?)",
        "preview.session_gone": "Ta sesja sie zakonczyla - pokazuje ostatni znany transkrypt.",
        "preview.subagent_entry": "     ↳ subagent {name} (zapis {age} temu)",
        "preview.no_events": "W transkrypcie nie ma jeszcze rekordow rozmowy.",
        "power.timeout": "{label} - komenda nie skonczyla sie w {seconds}s i zostala ubita: {cmd}",
        "log.crash": "AWARIA: {error} (pelny traceback w crash.log)",
        "crash.title": "Claude AutoShutdown - awaria",
        "crash.body": "Program napotkal nieoczekiwany blad i JUZ NIE PILNUJE.\n\nSzczegoly: {path}",
        "state.not_writable": "Nie moge pisac do katalogu programu:\n{path}\n\nBez tego nie ma "
                              "logu, blokady ani hamulca STOP. Przenies program albo ustaw "
                              "CLAUDE_AUTOSHUTDOWN_HOME.",
        "dry.title": "Tryb prob",
        "dry.body": "Warunki spelnione - w trybie bojowym komputer zostalby teraz obsluzony "
                    "akcja: {label}.",
        "action.failed_title": "Akcja nie powiodla sie",
        "action.failed_body": "{detail}\n\nProgram sie rozbroil. Szczegoly w zakladce Log.",
        "action.stale_title": "Akcja wstrzymana",
        "action.stale_body": "Skaner przestal dostarczac dane, wiec nie wiem, czy sesje nadal "
                             "pracuja.\n\nProgram sie rozbroil zamiast zgadywac.",
        "close.title": "Zamknac?",
        "close.body": "Program jest UZBROJONY. Zamkniecie anuluje pilnowanie. Zamknac?",
        "instance.title": "Program juz dziala",
        "instance.body": "Claude AutoShutdown dziala juz w procesie {pid}.\n\n"
                         "Dwie instancje moglyby wykonac akcje dwa razy, wiec ta sie zamyka.",
        "log.start": "Start programu (ROZBROJONY, {mode}, akcja: {action}, cisza {quiet}s, "
                     "bezczynnosc {idle}s)",
        "log.mode_dry": "tryb prob",
        "log.mode_live": "TRYB BOJOWY",
        "log.autoarm_env": "AUTOARM: uzbrojono bez potwierdzenia (CLAUDE_AUTOSHUTDOWN_AUTOARM=1) "
                           "- tryb testu koncowego",
        "log.autoarm_cfg": "Uzbrojono automatycznie przy starcie (arm_on_start=true) - akcja: "
                           "{action}, tryb prob: {dry}",
        "log.settings_saved": "Ustawienia zapisane: {cfg}",
        "log.disarmed": "ROZBROJONY (recznie)",
        "log.arm_refused": "Odmowa uzbrojenia - brak uprawnien: {why}",
        "log.armed": "UZBROJONY - akcja={action}, dry_run={dry}",
        "log.scanner_warning": "Uwaga skanera: {error}",
        "log.monitor_error": "Blad watku monitora: {error}",
        "log.gui_error": "Blad petli GUI: {error}",
        "log.waiting": "Czekam, bo: {blockers}",
        "log.all_green": "Wszystkie warunki zielone",
        "log.countdown_start": "Warunki spelnione {a}/{b} - start odliczania {s} s",
        "log.countdown_cancelled": "Odliczanie ANULOWANE: {reason}",
        "log.skipped_disarmed": "Akcja pominieta - program jest rozbrojony",
        "log.skipped_stop": "Akcja pominieta - pojawil sie plik STOP",
        "log.withheld_stale": "Akcja WSTRZYMANA - dane skanera nieswieze ({d}), watek zyje: {alive}",
        "log.dry_run": "TRYB PROB: tutaj poszloby '{label}'. Odznacz 'Tryb prob' w Ustawieniach, "
                       "zeby dzialalo naprawde.",
        "log.action_done": "AKCJA WYKONANA: {detail}",
        "log.action_failed": "AKCJA NIEUDANA: {detail}",
        "log.closing": "Zamkniecie programu",
        "log.language": "Jezyk przelaczony na {lang}",
    },
}


def missing_keys() -> dict[str, list[str]]:
    """Klucze, ktorych brakuje w ktoryms jezyku wzgledem angielskiego. Do testu."""
    base = set(STRINGS[DEFAULT_LANGUAGE])
    return {code: sorted(base - set(table))
            for code, table in STRINGS.items() if code != DEFAULT_LANGUAGE}
