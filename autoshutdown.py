"""Claude AutoShutdown - wylacza komputer dopiero gdy WSZYSTKIE sesje

Claude Code / Cowork (razem z subagentami) faktycznie przestana pracowac.

Bezpieczniki, zeby wylaczenie nigdy nie bylo przypadkowe:
  1. Start zawsze w stanie ROZBROJONY (nigdy dziedziczony z pliku);
     tryb prob wlaczony domyslnie, ale swiadome zdjecie go przezywa restart.
  2. Uzbrojenie wymaga swiadomego kliknicia i potwierdzenia regul.
  3. Kazdy warunek musi byc zdany N razy z rzedu (domyslnie 3 cykle).
  4. Odliczanie z duzym ANULUJ; powrot dowolnej sesji do pracy przerywa je sam.
  5. Plik STOP w katalogu programu blokuje akcje niezaleznie od wszystkiego.
  6. Kazda decyzja ladu je w autoshutdown.log.
"""

from __future__ import annotations

import ctypes
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
import traceback
from datetime import UTC, datetime
from pathlib import Path
from tkinter import messagebox, ttk

import winprobe
from i18n import AUTO, LANGUAGES, current_language, set_language, t
from monitor import (
    Session,
    SessionScanner,
    Verdict,
    claude_processes_without_registry,
    describe_event,
    evaluate,
    fmt_duration,
    matching_guard_processes,
    tail_events,
)


def enable_dpi_awareness() -> None:
    """Bez tego Windows rozciaga okno bitmapowo - rozmyty tekst i uciete kolumny.

    SetProcessDpiAwareness zwraca HRESULT zamiast rzucac wyjatek, wiec sam try/except
    nie wystarczy: na starszym Windowsie dostalibysmy cichy blad i zaden fallback.
    """
    if sys.platform != "win32":
        return
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:  # per-monitor v2
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


# Katalog stanu (config, log, plik STOP). Domyslnie obok programu; nadpisywalny,
# zeby dalo sie uruchomic instancje testowa bez dotykania prawdziwej konfiguracji.
def _default_app_dir() -> Path:
    """Katalog stanu obok programu.

    Pod PyInstallerem (onefile) __file__ wskazuje na tymczasowy katalog _MEIPASS,
    kasowany po zamknieciu - config, log i STOP znikalyby z kazdym uruchomieniem.
    Wtedy wlasciwym "obok programu" jest katalog pliku .exe.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = Path(os.environ.get("CLAUDE_AUTOSHUTDOWN_HOME") or _default_app_dir())
APP_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = APP_DIR / "config.json"
LOG_PATH = APP_DIR / "autoshutdown.log"
STOP_FILE = APP_DIR / "STOP"

# Tryb testu koncowego: uzbraja bez okienka potwierdzenia. Wlaczany WYLACZNIE przez
# zmienna srodowiskowa i glosno logowany - normalne uruchomienie go nie widzi.
AUTOARM = os.environ.get("CLAUDE_AUTOSHUTDOWN_AUTOARM") == "1"

LOCK_FILE = APP_DIR / "instance.lock"
LOG_MAX_BYTES = 5 * 1024 * 1024
COUNTDOWN_COOLDOWN_SECONDS = 60.0     # po anulowaniu nie startujemy od razu kolejnego
PROCESS_SCAN_IDLE_SECONDS = 60.0      # tasklist gdy rozbrojony: co minute, nie co cykl

DEFAULT_CONFIG: dict = {
    "poll_seconds": 10,
    "quiet_seconds": 300,
    "required_polls": 3,
    "countdown_seconds": 90,
    "action": "shutdown",
    "dry_run": True,
    "require_human_idle": True,
    "human_idle_required": 600,
    "allow_zero_sessions": False,
    "arm_on_start": False,
    "force_close_apps": True,
    "language": "auto",  # jezyk systemu, jesli go mamy; inaczej angielski
    "guard_patterns": [],
}

# Paleta - ciemny motyw, czytelny na duzym monitorze.
BG = "#14181d"
BG_PANEL = "#1c222a"
BG_ROW = "#232b35"
FG = "#e6edf3"
FG_DIM = "#8b98a5"
ACCENT = "#255b7d"
OK_COLOR = "#3fb950"
WARN_COLOR = "#d29922"
BAD_COLOR = "#f85149"


# Ostrzezenia z ostatniego load_config() - GUI wypisuje je do logu przy starcie,
# zeby recznie zepsuty config.json nie byl cicho "naprawiany" bez sladu.
CONFIG_WARNINGS: list[str] = []

_INT_BOUNDS: dict[str, tuple[int, int]] = {
    # klucz: (minimum, maksimum) - dolne granice to bezpieczniki, nie estetyka:
    # countdown 0 = brak szansy na ANULUJ, poll 0 = petla zajeta, polls 0 = brak potwierdzen
    "quiet_seconds": (10, 86_400),
    "poll_seconds": (2, 3_600),
    "required_polls": (1, 100),
    "countdown_seconds": (5, 3_600),
    "human_idle_required": (0, 86_400),
}
_BOOL_KEYS = ("dry_run", "require_human_idle", "allow_zero_sessions",
              "arm_on_start", "force_close_apps")
_TRUE_WORDS = {"true", "yes", "on", "1"}
_FALSE_WORDS = {"false", "no", "off", "0", ""}


def _coerce_bool(key: str, value: object, warnings: list[str]) -> bool:
    """bool() na napisie "false" daje True - to wlaczaloby najgrozniejsze opcje."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _TRUE_WORDS:
            return True
        if word in _FALSE_WORDS:
            return False
    warnings.append(f"{key}: niezrozumiala wartosc {value!r}, uzywam domyslnej")
    return bool(DEFAULT_CONFIG[key])


def _coerce_int(key: str, value: object, warnings: list[str]) -> int:
    lo, hi = _INT_BOUNDS[key]
    try:
        number = int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        warnings.append(f"{key}: {value!r} nie jest liczba, uzywam domyslnej")
        return int(DEFAULT_CONFIG[key])
    if number < lo or number > hi:
        warnings.append(f"{key}: {number} poza zakresem {lo}-{hi}, przycinam")
    return max(lo, min(number, hi))


def validate_config(raw: dict) -> tuple[dict, list[str]]:
    """Zwraca (bezpieczna konfiguracja, lista ostrzezen). Nigdy nie rzuca."""
    warnings: list[str] = []
    cfg = dict(DEFAULT_CONFIG)
    for key in _BOOL_KEYS:
        if key in raw:
            cfg[key] = _coerce_bool(key, raw[key], warnings)
    for key in _INT_BOUNDS:
        if key in raw:
            cfg[key] = _coerce_int(key, raw[key], warnings)

    action = str(raw.get("action") or DEFAULT_CONFIG["action"])
    if action not in winprobe.POWER_ACTIONS:
        # Nieznana akcja NIE moze cicho zamienic sie w "wylacz komputer".
        warnings.append(f"action: nieznana wartosc {action!r}, przelaczam na 'nothing'")
        action = "nothing"
    cfg["action"] = action

    guards = raw.get("guard_patterns", [])
    if isinstance(guards, str):
        guards = [p.strip() for p in guards.split(",") if p.strip()]
    elif isinstance(guards, list):
        guards = [str(p).strip() for p in guards if str(p).strip()]
    else:
        warnings.append("guard_patterns: oczekiwana lista, ignoruje")
        guards = []
    cfg["guard_patterns"] = guards

    language = raw.get("language", DEFAULT_CONFIG["language"])
    cfg["language"] = str(language) if isinstance(language, str) else DEFAULT_CONFIG["language"]
    return cfg, warnings


def load_config() -> dict:
    """Czyta config.json i zawsze zwraca bezpieczna konfiguracje.

    Tryb prob DZIEDZICZY sie z pliku - jesli uzytkownik swiadomie go zdjal, ma zostac
    zdjety. Tym, czego nigdy nie dziedziczymy, jest UZBROJENIE: program zawsze
    startuje rozbrojony (ClaudeAutoShutdown.armed = False).
    """
    raw: dict = {}
    warnings: list[str] = []
    if CONFIG_PATH.exists():
        try:
            loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # ValueError obejmuje JSONDecodeError i UnicodeDecodeError (plik w ANSI).
            warnings.append(f"config.json nieczytelny ({exc}), uzywam domyslnych")
            loaded = {}
        if isinstance(loaded, dict):
            raw = loaded
        else:
            warnings.append("config.json nie jest obiektem JSON, uzywam domyslnych")
    cfg, more = validate_config(raw)
    CONFIG_WARNINGS[:] = warnings + more
    set_language(str(cfg.get("language") or AUTO))
    return cfg


def save_config(cfg: dict) -> None:
    """Zapis atomowy: przerwany zapis nie zostawia polowy pliku (= domyslne = shutdown)."""
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)


def stop_file_present() -> bool:
    """Hamulec: STOP, STOP.txt, stop.txt... - Eksplorator dokleja rozszerzenie po cichu."""
    try:
        return any(p.name.lower() in ("stop", "stop.txt") for p in APP_DIR.iterdir())
    except OSError:
        return True  # nie umiem sprawdzic hamulca = traktuje jak zaciagniety


def rotate_log_if_needed() -> None:
    """Program chodzi miesiacami - bez rotacji log rosnie bez konca."""
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_MAX_BYTES:
            backup = LOG_PATH.with_suffix(".log.1")
            backup.unlink(missing_ok=True)
            LOG_PATH.rename(backup)
    except OSError:
        pass


def log_line(text: str) -> str:
    stamp = datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {text}"
    try:
        rotate_log_if_needed()
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    return line


def another_instance_running() -> int | None:
    """PID innej zywej instancji albo None.

    Dwie uzbrojone instancje = dwa odliczania i dwie akcje zasilania. Blokada
    uzywa tego samego sprawdzenia co reszta programu: PID musi zyc i byc TYM
    SAMYM procesem (czas startu), inaczej to smiec zostawiony po awarii.
    """
    try:
        raw = LOCK_FILE.read_text(encoding="utf-8").split()
        pid, started = int(raw[0]), int(raw[1])
    except (OSError, ValueError, IndexError):
        return None
    info = winprobe.probe_process(pid)
    if info.alive and abs(info.created_filetime - started) <= 10_000_000:
        return pid
    return None


def claim_instance_lock() -> None:
    info = winprobe.probe_process(os.getpid())
    try:
        LOCK_FILE.write_text(f"{os.getpid()} {info.created_filetime}", encoding="utf-8")
    except OSError:
        pass  # brak blokady jest lepszy niz brak programu


# --------------------------------------------------------------------------- #
# Watek monitorujacy
# --------------------------------------------------------------------------- #
class MonitorThread(threading.Thread):
    """Skanuje w tle i wrzuca migawki do kolejki. GUI tylko czyta."""

    def __init__(self, app: ClaudeAutoShutdown) -> None:
        super().__init__(daemon=True, name="monitor")
        self.app = app
        self.results: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.scanner = SessionScanner()
        self.stable_polls = 0
        self.saw_any_session = False
        self.last_session_seen = 0.0
        self.last_process_scan = 0.0
        self.cached_guard_hits: list[str] | None = []
        self.cached_stray_pids: list[int] = []

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    def reset_stability(self) -> None:
        self.stable_polls = 0

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self._one_cycle()
            except Exception:  # noqa: BLE001 - watek pilnujacy nie moze umrzec po cichu
                self.results.put(("error", traceback.format_exc()))
            # Konwersja MUSI byc odporna: "poll_seconds": "co 10 s" w recznie
            # edytowanym config.json zabijalo watek skanera po cichu.
            try:
                delay = max(2, int(float(self.app.cfg["poll_seconds"])))
            except (TypeError, ValueError):
                delay = int(DEFAULT_CONFIG["poll_seconds"])
            self._wake.wait(timeout=delay)
            self._wake.clear()

    def _one_cycle(self) -> None:
        cfg = self.app.cfg
        sessions = self.scanner.scan(quiet_seconds=float(cfg["quiet_seconds"]))
        if sessions:
            self.saw_any_session = True
            self.last_session_seen = time.time()

        patterns = list(cfg.get("guard_patterns") or [])
        # tasklist kosztuje ~0,5 s. Rozbrojony program nie potrzebuje go co 10 s -
        # odswiezamy wtedy co PROCESS_SCAN_IDLE_SECONDS, a zawsze gdy uzbrojony.
        now = time.time()
        if self.app.armed or now - self.last_process_scan >= PROCESS_SCAN_IDLE_SECONDS:
            self.last_process_scan = now
            self.cached_guard_hits = matching_guard_processes(patterns)
            self.cached_stray_pids = (
                claude_processes_without_registry({s.pid for s in sessions})
                if self.scanner.is_default_root else [])
        verdict = evaluate(
            sessions,
            quiet_seconds=float(cfg["quiet_seconds"]),
            armed=self.app.armed,
            stop_file_present=stop_file_present(),
            human_idle=winprobe.human_idle_seconds(),
            require_human_idle=bool(cfg["require_human_idle"]),
            human_idle_required=float(cfg["human_idle_required"]),
            allow_zero_sessions=bool(cfg["allow_zero_sessions"]),
            saw_any_session=self.saw_any_session,
            guard_patterns=patterns,
            guard_hits=self.cached_guard_hits,
            stable_polls=self.stable_polls,
            required_polls=int(cfg["required_polls"]),
            scan_error=self.scanner.last_error,
            seconds_since_last_session=(time.time() - self.last_session_seen
                                        if self.last_session_seen else 0.0),
            unregistered_pids=self.cached_stray_pids,
        )
        self.stable_polls = verdict.stable_polls
        self.results.put(("snapshot", (sessions, verdict, self.scanner.last_error)))


# --------------------------------------------------------------------------- #
# Okno odliczania
# --------------------------------------------------------------------------- #
class CountdownWindow(tk.Toplevel):
    """Ostatnia bramka przed akcja. Zamkniecie okna = anulowanie."""

    def __init__(self, app: ClaudeAutoShutdown, seconds: int, action_label: str,
                 action: str, dry_run: bool) -> None:
        super().__init__(app.root)
        self.app = app
        self.remaining = max(1, seconds)
        self.cancelled = False
        # Akcja i tryb sa ZAMROZONE w chwili startu odliczania. Wczesniej okno
        # pokazywalo "[DRY RUN]", a execute_action czytal config ponownie po 90 s -
        # zapis ustawien w miedzyczasie mogl zamienic probe w prawdziwe wylaczenie.
        self.action = action
        self.dry_run = dry_run
        self.initial = self.remaining  # do "dzwon na starcie i w ostatnich 5 s"

        self.title(t("countdown.title"))
        self.configure(bg=BG)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW",
                      lambda: self.cancel(t("countdown.reason.close")))
        self.bind("<Escape>", lambda _e: self.cancel(t("countdown.reason.esc")))
        self.geometry(f"{app.px(600)}x{app.px(330)}")

        tk.Label(self, text=t("countdown.heading"),
                 bg=BG, fg=FG, font=("Segoe UI", 16, "bold")).pack(pady=(24, 4))
        tk.Label(self, text=action_label, bg=BG, fg=WARN_COLOR,
                 font=("Segoe UI", 12)).pack()

        self.counter = tk.Label(self, text=str(seconds), bg=BG, fg=BAD_COLOR,
                                font=("Segoe UI", 72, "bold"))
        self.counter.pack(pady=6)

        # Obietnica "ruch myszy przerwie akcje" jest prawdziwa tylko, gdy bramka
        # bezczynnosci uzytkownika jest wlaczona. Inaczej napis klamalby.
        note_key = ("countdown.note" if app.cfg.get("require_human_idle", True)
                    else "countdown.note_no_idle")
        self.note = tk.Label(self, text=t(note_key),
                             bg=BG, fg=FG_DIM, font=("Segoe UI", 9))
        self.note.pack()

        row = tk.Frame(self, bg=BG)
        row.pack(pady=16)
        self.cancel_button = tk.Button(
            row, text=t("countdown.cancel"), command=lambda: self.cancel(t("countdown.reason.button")),
            bg=OK_COLOR, fg="#0b0f14", font=("Segoe UI", 13, "bold"), width=16,
            relief="flat", cursor="hand2")
        self.cancel_button.pack(side="left", padx=8)
        # "Wykonaj teraz" celowo nie przyjmuje focusu klawiatury - przypadkowa spacja
        # ma anulowac, nigdy przyspieszyc wylaczenie.
        tk.Button(row, text=t("countdown.now"), command=self.fire, bg=BG_ROW, fg=FG,
                  font=("Segoe UI", 10), width=14, relief="flat", takefocus=0,
                  cursor="hand2").pack(side="left", padx=8)

        self.lift()
        self.cancel_button.focus_set()

    def start(self) -> None:
        """Odliczanie rusza DOPIERO gdy aplikacja ma juz referencje do okna.

        Wczesniej __init__ wolal _tick() sam - przy countdown_seconds<=0 akcja
        odpalala z okna, ktorego app.countdown jeszcze nie znal, wiec anulowanie
        nie mialo czego anulowac.
        """
        self._tick()

    def _tick(self) -> None:
        if self.cancelled:
            return
        if self.remaining <= 0:
            self.fire()
            return
        self.counter.config(text=str(self.remaining))
        # Dzwiek na starcie i w ostatnich 5 s - nie co sekunde przez 90 s.
        if self.remaining == self.initial or self.remaining <= 5:
            try:
                self.bell()
            except tk.TclError:
                pass
        self.remaining -= 1
        self.after(1000, self._tick)

    def cancel(self, reason: str = "") -> None:
        reason = reason or t("countdown.reason.unknown")
        if self.cancelled:
            return
        self.cancelled = True
        self.app.on_countdown_cancelled(reason)
        self.destroy()

    def fire(self) -> None:
        if self.cancelled:
            return
        self.cancelled = True
        self.destroy()
        self.app.execute_action(self.action, self.dry_run)


# --------------------------------------------------------------------------- #
# Aplikacja
# --------------------------------------------------------------------------- #
class ClaudeAutoShutdown:
    def __init__(self) -> None:
        self.cfg = load_config()
        self.armed = False
        self.sessions: list[Session] = []
        self.verdict: Verdict | None = None
        self.countdown: CountdownWindow | None = None
        self.closing = False
        self.last_snapshot = time.time()
        self.last_cancel = 0.0
        self.last_blockers: list[str] = []
        self.last_scan_error = ""
        self._preview_rendered: tuple[str, float] | None = None
        self._restoring_selection = False
        self.selected_transcript: Path | None = None  # None = glowny transkrypt sesji
        self._combo_index: dict[str, tuple[str, Path | None]] = {}
        self.log_lines: list[str] = []
        self.selected_session_id: str | None = None

        enable_dpi_awareness()
        self.root = tk.Tk()
        # Wszystkie rozmiary podajemy logicznie i mnozymy przez skale monitora.
        self.dpi = self.root.winfo_fpixels("1i") / 96.0
        self.root.title("Claude AutoShutdown")
        self._set_window_icon()
        # Rozmiar startowy przyciety do ekranu: na malym laptopie okno 1400x820
        # pomnozone przez skalowanie DPI wychodzilo poza obszar roboczy.
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        width = min(self.px(1400), int(screen_w * 0.95))
        height = min(self.px(820), int(screen_h * 0.9))
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(min(self.px(1000), width), min(self.px(620), height))
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_style()
        self._build_header()
        self._build_body()
        # Wpis MUSI opisywac rzeczywisty stan - staly napis "tryb prob" klamalby
        # dokladnie wtedy, gdy program potrafi naprawde wylaczyc komputer.
        self._append_log(log_line(t(
            "log.start",
            mode=t("log.mode_dry") if self.cfg["dry_run"] else t("log.mode_live"),
            action=winprobe.POWER_ACTIONS[self.cfg["action"]].label,
            quiet=self.cfg["quiet_seconds"], idle=self.cfg["human_idle_required"])))

        if AUTOARM:
            self.armed = True
            self._append_log(log_line(t("log.autoarm_env")))
            self._render_header()
        elif self.cfg.get("arm_on_start"):
            # Swiadome oslabienie bezpiecznika nr 1: program uzbraja sie sam po
            # starcie, zeby po restarcie komputera nie trzeba bylo nic klikac.
            # Reszta bramek (cisza, tury, bezczynnosc, odliczanie, STOP) zostaje.
            self.armed = True
            self._append_log(log_line(t(
                "log.autoarm_cfg", action=winprobe.POWER_ACTIONS[self.cfg["action"]].label,
                dry=self.cfg["dry_run"])))
            self._render_header()

        self.monitor = MonitorThread(self)
        self.monitor.start()
        self.root.after(300, self._drain_queue)
        self.root.after(1000, self._tick_preview)
        self.root.after(5000, self._check_monitor_alive)

    def _set_window_icon(self) -> None:
        """Wlasna ikona zamiast piorka tkintera - w pasku tytulu i na pasku zadan.

        Pod PyInstallerem zasoby leza w tymczasowym _MEIPASS, w zrodlach obok pliku.
        Brak ikony nie jest bledem krytycznym - program ma dzialac i bez niej.
        """
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        icon = base / "assets" / "icon.ico"
        if not icon.exists():
            return
        try:
            self.root.iconbitmap(default=str(icon))
        except tk.TclError:
            pass

    def px(self, logical: int) -> int:
        """Logiczne piksele -> fizyczne, wedlug skalowania monitora."""
        return round(logical * self.dpi)

    # --- wyglad ------------------------------------------------------------
    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", background=BG_ROW, fieldbackground=BG_ROW,
                        foreground=FG, rowheight=self.px(26), borderwidth=0,
                        font=("Segoe UI", 10))
        style.configure("Treeview.Heading", background=BG_PANEL, foreground=FG_DIM,
                        borderwidth=0, font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", ACCENT)],
                  foreground=[("selected", "#ffffff")])
        style.configure("TCombobox", fieldbackground=BG_ROW, background=BG_ROW)

    def _build_header(self) -> None:
        head = tk.Frame(self.root, bg=BG_PANEL, height=self.px(94))
        head.pack(fill="x", side="top")
        head.pack_propagate(False)

        right = tk.Frame(head, bg=BG_PANEL)
        right.pack(side="right", padx=18)
        self.arm_button = tk.Button(right, text=t("btn.arm"), command=self.toggle_arm,
                                    bg=OK_COLOR, fg="#0b0f14", width=13, relief="flat",
                                    font=("Segoe UI", 12, "bold"), cursor="hand2")
        self.arm_button.pack(side="right", padx=(10, 0), pady=18)
        self.counts_label = tk.Label(right, text="", bg=BG_PANEL, fg=FG,
                                     font=("Segoe UI", 10))
        self.counts_label.pack(side="right", padx=12)
        # Przelacznik jezyka: pokazuje TEN DRUGI jezyk (klik = przejdz na niego).
        tk.Button(right, text=t("btn.lang"), command=self.toggle_language, bg=BG_ROW,
                  fg=FG, width=4, relief="flat", font=("Segoe UI", 9, "bold"),
                  cursor="hand2").pack(side="right", padx=(0, 6))

        left = tk.Frame(head, bg=BG_PANEL)
        left.pack(side="left", padx=18, pady=12, fill="both", expand=True)

        self.state_label = tk.Label(left, text=t("header.disarmed"), bg=BG_PANEL, fg=FG_DIM,
                                    font=("Segoe UI", 15, "bold"))
        self.state_label.pack(anchor="w")
        self.verdict_label = tk.Label(left, text=t("header.starting"), bg=BG_PANEL,
                                      fg=FG_DIM, font=("Segoe UI", 10))
        self.verdict_label.pack(anchor="w", pady=(2, 0), fill="x")

    def _build_body(self) -> None:
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True)

        nav = tk.Frame(body, bg=BG_PANEL, width=self.px(168))
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)

        self.pages: dict[str, tk.Frame] = {}
        self.nav_buttons: dict[str, tk.Button] = {}
        self.content = tk.Frame(body, bg=BG)
        self.content.pack(side="left", fill="both", expand=True)

        for key, label in (("monitor", t("nav.monitor")), ("preview", t("nav.preview")),
                           ("settings", t("nav.settings")), ("log", t("nav.log"))):
            btn = tk.Button(nav, text=label, command=lambda k=key: self.show_page(k),
                            bg=BG_PANEL, fg=FG_DIM, relief="flat", anchor="w",
                            font=("Segoe UI", 11), padx=20, pady=12, cursor="hand2",
                            activebackground=BG_ROW, activeforeground=FG)
            btn.pack(fill="x")
            self.nav_buttons[key] = btn
            page = tk.Frame(self.content, bg=BG)
            self.pages[key] = page

        tk.Label(nav, text=t("nav.stop_hint"),
                 bg=BG_PANEL, fg=FG_DIM, font=("Segoe UI", 8), justify="left",
                 anchor="w").pack(side="bottom", fill="x", padx=16, pady=14)

        self._build_monitor_page(self.pages["monitor"])
        self._build_preview_page(self.pages["preview"])
        self._build_settings_page(self.pages["settings"])
        self._build_log_page(self.pages["log"])
        self.show_page("monitor")

    def show_page(self, key: str) -> None:
        for name, page in self.pages.items():
            page.pack_forget()
            self.nav_buttons[name].config(bg=BG_PANEL, fg=FG_DIM)
        self.pages[key].pack(fill="both", expand=True)
        self.nav_buttons[key].config(bg=BG_ROW, fg=FG)

    # --- strona: monitor ---------------------------------------------------
    def _build_monitor_page(self, page: tk.Frame) -> None:
        tk.Label(page, text=t("monitor.title"), bg=BG, fg=FG,
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=18, pady=(16, 6))

        columns = ("state", "name", "where", "why", "silence", "cpu", "subs", "pid")
        wrap = tk.Frame(page, bg=BG)
        wrap.pack(fill="x", padx=18)
        self.tree = ttk.Treeview(wrap, columns=columns, show="headings", height=9)
        headings = {
            "state": (t("col.state"), 95), "name": (t("col.name"), 125),
            "where": (t("col.where"), 180), "why": (t("col.why"), 250),
            "silence": (t("col.silence"), 80), "cpu": (t("col.cpu"), 60),
            "subs": (t("col.subs"), 90), "pid": (t("col.pid"), 60),
        }
        for col, (title, width) in headings.items():
            self.tree.heading(col, text=title)
            # stretch=False: kolumny nie rozjezdzaja sie przy zmianie rozmiaru okna,
            # a to, co sie nie miesci, jest dostepne paskiem - nigdy ciche obciecie.
            self.tree.column(col, width=self.px(width), minwidth=self.px(50),
                             anchor="w", stretch=False)
        self.tree.tag_configure("working", foreground="#ffd479")
        self.tree.tag_configure("idle", foreground=OK_COLOR)
        xscroll = ttk.Scrollbar(wrap, orient="horizontal", command=self.tree.xview)
        self.tree.configure(xscrollcommand=xscroll.set)
        self.tree.pack(fill="x")
        xscroll.pack(fill="x")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", lambda _e: self.show_page("preview"))

        tk.Label(page, text=t("monitor.conditions"), bg=BG,
                 fg=FG, font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=18,
                                                            pady=(18, 6))
        self.checks_frame = tk.Frame(page, bg=BG_PANEL)
        self.checks_frame.pack(fill="both", expand=True, padx=18, pady=(0, 18))

    def _on_tree_select(self, _event: object) -> None:
        if getattr(self, "_restoring_selection", False):
            return
        selection = self.tree.selection()
        if selection:
            self.selected_session_id = selection[0]
            self.selected_transcript = None
            self.preview_combo.set(self._combo_label_for(selection[0]))

    # --- strona: podglad ---------------------------------------------------
    def _build_preview_page(self, page: tk.Frame) -> None:
        top = tk.Frame(page, bg=BG)
        top.pack(fill="x", padx=18, pady=(16, 8))
        tk.Label(top, text=t("preview.session"), bg=BG, fg=FG_DIM,
                 font=("Segoe UI", 10)).pack(side="left")
        self.preview_combo = ttk.Combobox(top, state="readonly", width=52)
        self.preview_combo.pack(side="left", padx=8)
        self.preview_combo.bind("<<ComboboxSelected>>", self._on_combo_select)
        self.preview_status = tk.Label(top, text="", bg=BG, fg=FG_DIM,
                                       font=("Segoe UI", 9))
        self.preview_status.pack(side="left", padx=12)

        wrap = tk.Frame(page, bg=BG_PANEL)
        wrap.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        self.preview_text = tk.Text(wrap, bg=BG_PANEL, fg=FG, relief="flat", wrap="word",
                                    font=("Consolas", 9), padx=12, pady=10,
                                    insertbackground=FG)
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.preview_text.yview)
        self.preview_text.configure(yscrollcommand=scroll.set, state="disabled")
        scroll.pack(side="right", fill="y")
        self.preview_text.pack(fill="both", expand=True)
        self.preview_text.tag_configure("time", foreground=FG_DIM)
        self.preview_text.tag_configure("who", foreground="#79c0ff")
        self.preview_text.tag_configure("tool", foreground="#d2a8ff")

    def _on_combo_select(self, _event: object) -> None:
        label = self.preview_combo.get()
        target = self._combo_index.get(label)
        if target is not None:
            self.selected_session_id, self.selected_transcript = target
        self._refresh_preview(force=True)

    def _combo_label_for(self, session_id: str) -> str:
        for session in self.sessions:
            if session.session_id == session_id:
                return f"{session.name} - {session.short_cwd} ({session.surface}, PID {session.pid})"
        return session_id[:12]

    def _subagent_label(self, path: Path, mtime: float) -> str:
        return t("preview.subagent_entry", name=path.stem,
                 age=fmt_duration(max(0.0, time.time() - mtime)))

    # --- strona: ustawienia ------------------------------------------------
    def _build_settings_page(self, page: tk.Frame) -> None:
        wrap = tk.Frame(page, bg=BG)
        wrap.pack(fill="both", expand=True, padx=24, pady=18)
        self.vars: dict[str, tk.Variable] = {}

        def add_row(row: int, key: str, label: str, hint: str) -> None:
            tk.Label(wrap, text=label, bg=BG, fg=FG,
                     font=("Segoe UI", 10)).grid(row=row, column=0, sticky="w", pady=6)
            var = tk.StringVar(value=str(self.cfg[key]))
            self.vars[key] = var
            tk.Entry(wrap, textvariable=var, width=12, bg=BG_ROW, fg=FG, relief="flat",
                     insertbackground=FG).grid(row=row, column=1, sticky="w", padx=10)
            tk.Label(wrap, text=hint, bg=BG, fg=FG_DIM,
                     font=("Segoe UI", 9)).grid(row=row, column=2, sticky="w")

        add_row(0, "quiet_seconds", t("settings.quiet"), t("settings.quiet.hint"))
        add_row(1, "poll_seconds", t("settings.poll"), t("settings.poll.hint"))
        add_row(2, "required_polls", t("settings.polls"), t("settings.polls.hint"))
        add_row(3, "countdown_seconds", t("settings.countdown"),
                t("settings.countdown.hint"))
        add_row(4, "human_idle_required", t("settings.human_idle"),
                t("settings.human_idle.hint"))

        row = 5
        tk.Label(wrap, text=t("settings.action"), bg=BG, fg=FG,
                 font=("Segoe UI", 10)).grid(row=row, column=0, sticky="w", pady=6)
        self.action_var = tk.StringVar(value=self.cfg["action"])
        action_box = ttk.Combobox(wrap, textvariable=self.action_var, state="readonly",
                                  width=28,
                                  values=[f"{k} - {a.label}"
                                          for k, a in winprobe.POWER_ACTIONS.items()])
        action_box.set(f"{self.cfg['action']} - "
                       f"{winprobe.POWER_ACTIONS[self.cfg['action']].label}")
        action_box.grid(row=row, column=1, columnspan=2, sticky="w", padx=10)

        row += 1
        self.dry_var = tk.BooleanVar(value=bool(self.cfg["dry_run"]))
        self.human_var = tk.BooleanVar(value=bool(self.cfg["require_human_idle"]))
        self.zero_var = tk.BooleanVar(value=bool(self.cfg["allow_zero_sessions"]))
        self.armstart_var = tk.BooleanVar(value=bool(self.cfg.get("arm_on_start")))
        self.force_var = tk.BooleanVar(value=bool(self.cfg.get("force_close_apps", True)))
        for var, text, hint in (
            (self.dry_var, t("settings.dry_run"), t("settings.dry_run.hint")),
            (self.human_var, t("settings.require_idle"), t("settings.require_idle.hint")),
            (self.zero_var, t("settings.zero_sessions"), t("settings.zero_sessions.hint")),
            (self.armstart_var, t("settings.arm_on_start"), t("settings.arm_on_start.hint")),
            (self.force_var, t("settings.force"), t("settings.force.hint")),
        ):
            tk.Checkbutton(wrap, text=text, variable=var, bg=BG, fg=FG, selectcolor=BG_ROW,
                           activebackground=BG, activeforeground=FG, relief="flat",
                           font=("Segoe UI", 10)).grid(row=row, column=0, columnspan=2,
                                                       sticky="w", pady=3)
            tk.Label(wrap, text=hint, bg=BG, fg=FG_DIM,
                     font=("Segoe UI", 9)).grid(row=row, column=2, sticky="w")
            row += 1

        tk.Label(wrap, text=t("settings.guards"), bg=BG, fg=FG,
                 font=("Segoe UI", 10)).grid(row=row, column=0, sticky="w", pady=6)
        self.guard_var = tk.StringVar(value=", ".join(self.cfg.get("guard_patterns") or []))
        tk.Entry(wrap, textvariable=self.guard_var, width=40, bg=BG_ROW, fg=FG,
                 relief="flat", insertbackground=FG).grid(row=row, column=1, columnspan=2,
                                                          sticky="w", padx=10)
        row += 1
        tk.Label(wrap, text=t("settings.guards.hint"),
                 bg=BG, fg=FG_DIM, font=("Segoe UI", 9)).grid(row=row, column=1,
                                                              columnspan=2, sticky="w",
                                                              padx=10)
        row += 1
        tk.Button(wrap, text=t("settings.save"), command=self.save_settings, bg=ACCENT,
                  fg="#ffffff", relief="flat", font=("Segoe UI", 10, "bold"), padx=18,
                  pady=6, cursor="hand2").grid(row=row, column=0, columnspan=2,
                                               sticky="w", pady=18)
        self.settings_status = tk.Label(wrap, text="", bg=BG, fg=OK_COLOR,
                                        font=("Segoe UI", 9))
        self.settings_status.grid(row=row, column=2, sticky="w")
        self.action_box = action_box

        row += 1
        can_shutdown, why = winprobe.shutdown_capability()
        states = winprobe.available_sleep_states()
        tk.Label(wrap, text=t("settings.privileges"), bg=BG, fg=FG,
                 font=("Segoe UI", 10, "bold")).grid(row=row, column=0, sticky="w",
                                                     pady=(10, 2))
        row += 1
        tk.Label(wrap, text=(t("settings.priv_ok") if can_shutdown
                             else t("settings.priv_missing")) + why, bg=BG,
                 fg=OK_COLOR if can_shutdown else BAD_COLOR,
                 font=("Segoe UI", 9)).grid(row=row, column=0, columnspan=3, sticky="w")
        if states:
            row += 1
            tk.Label(wrap, text=t("settings.power_states", states=states),
                     bg=BG, fg=FG_DIM, font=("Segoe UI", 9)).grid(
                row=row, column=0, columnspan=3, sticky="w")

    def save_settings(self) -> None:
        # Budujemy KOPIE i podmieniamy referencje jednym przypisaniem. Mutowanie
        # slownika w miejscu dawaloby watkowi monitora cykl z polowa starych,
        # polowa nowych ustawien.
        new_cfg = dict(self.cfg)
        try:
            for key in ("quiet_seconds", "poll_seconds", "required_polls",
                        "countdown_seconds", "human_idle_required"):
                new_cfg[key] = max(1, int(float(self.vars[key].get())))
        except ValueError:
            messagebox.showerror(t("settings.err_numbers"), t("settings.err_numbers.body"))
            return

        new_cfg["action"] = self.action_box.get().split(" - ")[0]
        new_cfg["dry_run"] = bool(self.dry_var.get())
        new_cfg["require_human_idle"] = bool(self.human_var.get())
        new_cfg["allow_zero_sessions"] = bool(self.zero_var.get())
        new_cfg["arm_on_start"] = bool(self.armstart_var.get())
        new_cfg["force_close_apps"] = bool(self.force_var.get())
        new_cfg["guard_patterns"] = [p.strip() for p in self.guard_var.get().split(",")
                                     if p.strip()]
        self.cfg = new_cfg
        # Zmiana ustawien w trakcie odliczania: okno pokazuje stara akcje/tryb,
        # wiec odliczanie trzeba przerwac, a nie pozwolic mu wykonac cos innego.
        if self.countdown is not None:
            self.countdown.cancel(t("countdown.reason.settings"))
        try:
            save_config(self.cfg)
        except OSError as exc:
            messagebox.showerror(t("settings.err_save"), str(exc))
            return
        self.monitor.reset_stability()
        self.monitor.wake()
        self.settings_status.config(text=t("settings.saved"))
        self.root.after(2500, lambda: self.settings_status.config(text=""))
        self._append_log(log_line(t("log.settings_saved",
                                    cfg=json.dumps(self.cfg, ensure_ascii=False))))

    # --- strona: log -------------------------------------------------------
    def _build_log_page(self, page: tk.Frame) -> None:
        wrap = tk.Frame(page, bg=BG_PANEL)
        wrap.pack(fill="both", expand=True, padx=18, pady=18)
        self.log_text = tk.Text(wrap, bg=BG_PANEL, fg=FG, relief="flat", wrap="word",
                                font=("Consolas", 9), padx=12, pady=10)
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set, state="disabled")
        scroll.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True)

    def _append_log(self, line: str) -> None:
        self.log_lines.append(line)
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # --- jezyk -------------------------------------------------------------
    def toggle_language(self) -> None:
        """Przelacza jezyk i przebudowuje interfejs w miejscu, bez restartu.

        Stan (sesje, werdykt, uzbrojenie, watek monitora) zyje w obiekcie, nie
        w widgetach, wiec zburzenie i odbudowanie okna nic nie gubi.
        """
        codes = list(LANGUAGES)
        new_code = codes[(codes.index(current_language()) + 1) % len(codes)]
        set_language(new_code)
        new_cfg = dict(self.cfg)
        new_cfg["language"] = new_code
        self.cfg = new_cfg
        try:
            save_config(self.cfg)
        except OSError:
            pass  # jezyk i tak dziala do konca sesji; brak zapisu nie blokuje UI
        for child in list(self.root.winfo_children()):
            if child is not self.countdown:
                child.destroy()
        self._build_header()
        self._build_body()
        self._render_all()
        self._append_log(log_line(t("log.language", lang=LANGUAGES[new_code])))

    # --- uzbrajanie --------------------------------------------------------
    def toggle_arm(self) -> None:
        if self.armed:
            self.armed = False
            self.monitor.reset_stability()
            self._append_log(log_line(t("log.disarmed")))
            # Rozbrojenie MUSI zatrzymac trwajace odliczanie - inaczej przycisk
            # klamie: napis zmienia sie na ROZBROJONY, a komputer i tak gasnie.
            if self.countdown is not None:
                self.countdown.cancel(t("countdown.reason.disarm"))
            self._render_header()
            return

        action_name = self.cfg["action"]
        action = winprobe.POWER_ACTIONS[action_name]
        label = action.label
        if action.needs_privilege:
            can, why = winprobe.shutdown_capability()
            if not can:
                self._append_log(log_line(t("log.arm_refused", why=why)))
                messagebox.showerror(t("arm.refused_title"),
                                     t("arm.refused_body", label=label, why=why))
                return
        mode = t("arm.mode_dry") if self.cfg["dry_run"] else t("arm.mode_live")
        summary = (
            t("arm.summary", label=label, mode=mode, quiet=self.cfg["quiet_seconds"],
              polls=self.cfg["required_polls"])
            + (t("arm.summary_idle", idle=self.cfg["human_idle_required"])
               if self.cfg["require_human_idle"] else "")
            + t("arm.summary_countdown", countdown=self.cfg["countdown_seconds"])
            + (t("arm.summary_force")
               if action_name == "shutdown" and self.cfg.get("force_close_apps", True)
               else "")
            + t("arm.summary_question")
        )
        if not messagebox.askyesno(t("arm.confirm_title"), summary, icon="warning"):
            return
        self.armed = True
        self.monitor.reset_stability()
        self.monitor.wake()
        self._append_log(log_line(t("log.armed", action=action_name, dry=self.cfg["dry_run"])))
        self._render_header()

    # --- petla GUI ---------------------------------------------------------
    def _drain_queue(self) -> None:
        """Petla GUI. Wyjatek NIE moze jej przerwac.

        Gdyby przerwal, okno zylo by dalej pokazujac zamrozone dane, a program
        po cichu przestalby pilnowac czegokolwiek - najgorszy mozliwy tryb awarii.
        """
        try:
            while True:
                kind, payload = self.monitor.results.get_nowait()
                if kind == "snapshot":
                    sessions, verdict, scan_error = payload
                    self.sessions = sessions
                    self.verdict = verdict
                    snapshot_at = self.last_snapshot = time.time()
                    # Trwaly blad skanera logujemy przy ZMIANIE tresci, nie co cykl -
                    # inaczej jeden zepsuty plik zalewa log tysiacami identycznych linii.
                    if scan_error != self.last_scan_error:
                        self.last_scan_error = scan_error
                        if scan_error:
                            self._append_log(log_line(t("log.scanner_warning",
                                                        error=scan_error)))
                    self._render_all()
                    self._log_blocker_change(verdict)
                    self._maybe_trigger(verdict, snapshot_at)
                elif kind == "error":
                    self._append_log(log_line(t("log.monitor_error",
                                                error=payload.splitlines()[-1])))
        except queue.Empty:
            pass
        except Exception:  # noqa: BLE001 - petla pilnujaca nie moze umrzec po cichu
            try:
                self._append_log(log_line(t("log.gui_error",
                                            error=traceback.format_exc().splitlines()[-1])))
            except tk.TclError:
                pass
        finally:
            if not self.closing:
                self.root.after(400, self._drain_queue)

    def _check_monitor_alive(self) -> None:
        """Ostrzega, gdy watek monitora przestal dostarczac migawki.

        Bez tego zamarly monitor wyglada jak "wszystko spokojnie".
        """
        if self.closing:
            return
        try:
            stale_after = max(30.0, 3 * float(self.cfg["poll_seconds"]))
            age = time.time() - self.last_snapshot
            if age > stale_after and self.monitor.is_alive():
                self.state_label.config(text=t("header.monitor_silent"), fg=BAD_COLOR)
                self.verdict_label.config(text=t("header.stale", d=fmt_duration(age)),
                                          fg=BAD_COLOR)
            elif not self.monitor.is_alive():
                self.state_label.config(text=t("header.monitor_dead"), fg=BAD_COLOR)
                self.verdict_label.config(text=t("header.dead"), fg=BAD_COLOR)
        except Exception:  # noqa: BLE001 - ostatni straznik nie moze umrzec razem z monitorem
            log_line(t("log.gui_error", error=traceback.format_exc().splitlines()[-1]))
        finally:
            self.root.after(5000, self._check_monitor_alive)

    def _log_blocker_change(self, verdict: Verdict) -> None:
        """Zapisuje, CO sie zmienilo w powodach czekania.

        Bez tego log mowil tylko o starcie i o akcji - a na pytanie "czemu rano
        komputer nadal chodzi" nie dalo sie odpowiedziec inaczej niz zgadywaniem.
        Logujemy przy zmianie zestawu blokerow, nie co cykl.
        """
        if not self.armed:
            self.last_blockers = []
            return
        current = verdict.blockers
        if current == self.last_blockers:
            return
        self.last_blockers = current
        if current:
            self._append_log(log_line(t("log.waiting",
                                        blockers=" | ".join(b[:120] for b in current))))
        else:
            self._append_log(log_line(t("log.all_green")))

    def _maybe_trigger(self, verdict: Verdict, snapshot_at: float = 0.0) -> None:
        if self.countdown is not None:
            # Odliczanie trwa - jesli cokolwiek przestalo byc czyste, przerywamy.
            confirm_label = t("check.confirmed", n=verdict.required_polls)
            if not all(passed for name, passed, _ in verdict.checks
                       if name != confirm_label):
                blockers = "; ".join(verdict.blockers)
                self.countdown.cancel(t("countdown.reason.conditions", blockers=blockers))
            return
        if verdict.ok and snapshot_at and snapshot_at <= self.last_cancel:
            # Migawka powstala PRZED anulowaniem - gdyby ja uwzglednic, ANULUJ
            # natychmiast otwieralby kolejne okno odliczania.
            return
        if verdict.ok and self.armed:
            # Cooldown po anulowaniu: bez niego odliczanie potrafilo restartowac sie
            # co cykl (zmierzone w audycie: 108 przerwanych odliczan w jednym logu),
            # z nowym oknem na wierzchu za kazdym razem.
            since_cancel = time.time() - self.last_cancel
            if self.last_cancel and since_cancel < COUNTDOWN_COOLDOWN_SECONDS:
                return
            action, dry_run = self.cfg["action"], bool(self.cfg["dry_run"])
            label = winprobe.POWER_ACTIONS[action].label
            if dry_run:
                label += f"  [{t('log.mode_dry').upper()}]"
            self._append_log(log_line(t(
                "log.countdown_start", a=verdict.stable_polls, b=verdict.required_polls,
                s=self.cfg["countdown_seconds"])))
            window = CountdownWindow(self, int(self.cfg["countdown_seconds"]), label,
                                     action=action, dry_run=dry_run)
            self.countdown = window
            window.start()

    def on_countdown_cancelled(self, reason: str) -> None:
        self.countdown = None
        self.last_cancel = time.time()
        self.monitor.reset_stability()
        self._append_log(log_line(t("log.countdown_cancelled", reason=reason)))
        self._render_header()

    def execute_action(self, action: str | None = None, dry_run: bool | None = None) -> None:
        self.countdown = None
        # Parametry przychodza z okna odliczania (zamrozone w chwili startu). Brak
        # parametrow = wywolanie spoza odliczania - bierzemy biezaca konfiguracje.
        action = action if action is not None else str(self.cfg["action"])
        dry_run = bool(self.cfg["dry_run"]) if dry_run is None else dry_run
        # Ostatnia bramka tuz przed akcja. Miedzy startem odliczania a ta chwila
        # uzytkownik mogl rozbroic program albo postawic plik STOP.
        if not self.armed:
            self._append_log(log_line(t("log.skipped_disarmed")))
            return
        if stop_file_present():
            self._append_log(log_line(t("log.skipped_stop")))
            self.armed = False
            self._render_header()
            return
        # Odliczanie leci na zegarze Tk, niezaleznie od skanera. Gdyby skaner umarl
        # tuz po starcie odliczania, nikt nie zauwazylby, ze sesja wrocila do pracy.
        snapshot_age = time.time() - self.last_snapshot
        max_age = max(30.0, 3 * float(self.cfg["poll_seconds"]))
        if snapshot_age > max_age or not self.monitor.is_alive():
            self._append_log(log_line(t("log.withheld_stale", d=fmt_duration(snapshot_age),
                                        alive=self.monitor.is_alive())))
            self.armed = False
            self._render_header()
            messagebox.showwarning(t("action.stale_title"), t("action.stale_body"))
            return
        if dry_run:
            label = winprobe.POWER_ACTIONS[action].label
            self._append_log(log_line(t("log.dry_run", label=label)))
            self.armed = False
            self.monitor.reset_stability()
            self._render_header()
            messagebox.showinfo(t("dry.title"), t("dry.body", label=label))
            return
        ok, detail = winprobe.power_action(
            action, force=bool(self.cfg.get("force_close_apps", True)))
        if ok:
            self._append_log(log_line(t("log.action_done", detail=detail)))
            # Po wykonanej akcji program sie rozbraja. Przy akcjach, po ktorych
            # komputer dalej chodzi (lock, sleep po wybudzeniu), uzbrojony program
            # zaczynalby kolejne odliczanie co cykl.
            self.armed = False
            self.monitor.reset_stability()
            self._render_header()
            return
        # Cicha porazka byla by najgorsza: uzytkownik mysli ze komputer zgasl,
        # a rano zastaje go wlaczonego bez sladu dlaczego.
        self._append_log(log_line(t("log.action_failed", detail=detail)))
        self.armed = False
        self.monitor.reset_stability()
        self._render_header()
        messagebox.showerror(t("action.failed_title"), t("action.failed_body", detail=detail))

    # --- rendering ---------------------------------------------------------
    def _render_all(self) -> None:
        self._render_header()
        self._render_tree()
        self._render_checks()
        self._render_combo()

    def _render_header(self) -> None:
        if self.armed:
            self.state_label.config(text=t("header.armed"), fg=OK_COLOR)
            self.arm_button.config(text=t("btn.disarm"), bg=BAD_COLOR, fg="#ffffff")
        else:
            self.state_label.config(text=t("header.disarmed"), fg=FG_DIM)
            self.arm_button.config(text=t("btn.arm"), bg=OK_COLOR, fg="#0b0f14")

        working = sum(1 for s in self.sessions if s.working)
        mode = t("header.mode_dry") if self.cfg["dry_run"] else t("header.mode_live")
        self.counts_label.config(
            text=t("header.counts", n=len(self.sessions), w=working, mode=mode))

        if self.verdict is None:
            return
        if self.verdict.ok:
            text, color = t("header.conditions_met"), OK_COLOR
        else:
            armed_label = t("check.armed") + ":"
            blockers = [b for b in self.verdict.blockers
                        if not b.startswith(armed_label)] or self.verdict.blockers
            first = blockers[0] if blockers else "?"
            if len(first) > 96:
                first = first[:93] + "..."
            text = t("header.waiting", reason=first)
            if len(blockers) > 1:
                text += t("header.more", n=len(blockers) - 1)
            color = WARN_COLOR if self.armed else FG_DIM
        self.verdict_label.config(text=text, fg=color)

    def _render_tree(self) -> None:
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for session in self.sessions:
            subs = (t("monitor.subs_active", n=session.active_subagents)
                    if session.active_subagents else "-")
            self.tree.insert(
                "", "end", iid=session.session_id,
                values=(session.state, session.name,
                        f"{session.short_cwd}  ({session.surface})", session.why,
                        fmt_duration(session.silence), f"{session.cpu_percent:.1f}%",
                        subs, session.pid),
                tags=("working" if session.working else "idle",))
        # Odtworzenie zaznaczenia odpala <<TreeviewSelect>>, ktory cofalby co cykl
        # wybor sesji zrobiony recznie w Podgladzie. Flaga wycisza handler.
        self._restoring_selection = True
        try:
            for iid in selected:
                if self.tree.exists(iid):
                    self.tree.selection_add(iid)
        finally:
            self._restoring_selection = False

    def _render_checks(self) -> None:
        for child in self.checks_frame.winfo_children():
            child.destroy()
        if self.verdict is None:
            return
        for name, passed, detail in self.verdict.checks:
            row = tk.Frame(self.checks_frame, bg=BG_PANEL)
            row.pack(fill="x", padx=14, pady=3)
            tk.Label(row, text=t("monitor.ok") if passed else t("monitor.no"), width=4,
                     bg=BG_PANEL, fg=OK_COLOR if passed else BAD_COLOR,
                     font=("Segoe UI", 9, "bold")).pack(side="left")
            tk.Label(row, text=name, bg=BG_PANEL, fg=FG, width=34, anchor="w",
                     font=("Segoe UI", 10)).pack(side="left")
            tk.Label(row, text=detail, bg=BG_PANEL, fg=FG_DIM, anchor="w",
                     font=("Segoe UI", 9)).pack(side="left", fill="x", expand=True)

    def _render_combo(self) -> None:
        """Lista w Podgladzie: sesja, a pod nia jej subagenci (najswiezsi pierwsi).

        Monitor pokazywal "13 subagentow pisze", a Podglad nie umial pokazac ani
        jednego z nich - teraz kazdy jest wpisem do wyboru.
        """
        index: dict[str, tuple[str, Path | None]] = {}
        for session in self.sessions:
            index[self._combo_label_for(session.session_id)] = (session.session_id, None)
            for path, mtime in session.subagent_files:
                index[self._subagent_label(path, mtime)] = (session.session_id, path)
        self._combo_index = index
        current = self.preview_combo.get()
        self.preview_combo.configure(values=list(index))
        if self.selected_session_id is None and self.sessions:
            self.selected_session_id = self.sessions[0].session_id
        # Etykieta subagenta zawiera wiek ("2m 10s temu"), wiec zmienia sie co cykl;
        # odnajdujemy biezacy wybor po sciezce, nie po tekscie.
        wanted = (self.selected_session_id, self.selected_transcript)
        for label, target in index.items():
            if target == wanted:
                if label != current:
                    self.preview_combo.set(label)
                return
        if self.selected_session_id and not current:
            self.preview_combo.set(self._combo_label_for(self.selected_session_id))

    # --- podglad transkryptu ----------------------------------------------
    def _tick_preview(self) -> None:
        if self.closing:
            return
        if self.pages["preview"].winfo_ismapped():
            self._refresh_preview()
        self.root.after(3000, self._tick_preview)

    def _refresh_preview(self, force: bool = False) -> None:
        session = next((s for s in self.sessions
                        if s.session_id == self.selected_session_id), None)
        if session is None:
            # Sesja zniknela - podglad nie moze dalej pokazywac "PRACUJE" na
            # starej tresci. Zostawiamy tekst, ale status mowi prawde.
            if self.selected_session_id:
                self.preview_status.config(text=t("preview.session_gone"))
            return
        transcript = self.selected_transcript or session.transcript
        if transcript is None:
            if force:
                self._set_preview_text(t("preview.no_transcript"))
            return

        try:
            mtime = transcript.stat().st_mtime
        except OSError:
            mtime = 0.0
        key = (str(transcript), mtime)
        if not force and key == self._preview_rendered:
            return  # nic sie nie zmienilo - nie kasujemy przewijania uzytkownika
        self._preview_rendered = key

        # Uzytkownik przewinal w gore? Zostaw go tam. Autoscroll tylko przy dole.
        at_bottom = self.preview_text.yview()[1] >= 0.999
        events = tail_events(transcript, count=45)
        lines: list[tuple[str, str, str]] = [describe_event(e) for e in events]
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        if not lines:
            self.preview_text.insert("end", t("preview.no_events"))
        for ts, who, text in lines:
            self.preview_text.insert("end", f"{ts} ", "time")
            self.preview_text.insert("end", f"{who}: ", "who")
            self.preview_text.insert("end", text + "\n",
                                     "tool" if text.startswith(("->", "<-")) else "")
        if at_bottom or force:
            self.preview_text.see("end")
        self.preview_text.configure(state="disabled")

        age = time.time() - (mtime if self.selected_transcript else session.last_activity)
        self.preview_status.config(
            text=t("preview.status", state=session.state, d=fmt_duration(age),
                   file=transcript.name))

    def _set_preview_text(self, text: str) -> None:
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("end", text)
        self.preview_text.configure(state="disabled")

    # --- zamkniecie --------------------------------------------------------
    def on_close(self) -> None:
        if self.armed and not messagebox.askyesno(t("close.title"), t("close.body")):
            return
        if self.countdown is not None:
            # Zamkniecie programu w trakcie odliczania ma zostawic slad w logu.
            self.countdown.cancel(t("countdown.reason.close_app"))
        self._append_log(log_line(t("log.closing")))
        self.closing = True
        self.monitor.stop()
        LOCK_FILE.unlink(missing_ok=True)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def _report_crash(exc_type, exc, tb) -> None:
    """Pod pythonw nie ma konsoli - nieobsluzony wyjatek ginalby bez sladu,
    a uzytkownik myslalby, ze program pilnuje. Zapis do logu + okno."""
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    log_line(t("log.crash", error=text.strip().splitlines()[-1]))
    try:
        (APP_DIR / "crash.log").write_text(text, encoding="utf-8")
    except OSError:
        pass
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(t("crash.title"), t("crash.body", path=APP_DIR / "crash.log"))
        root.destroy()
    except tk.TclError:
        pass


def state_dir_writable() -> bool:
    """Katalog stanu MUSI byc zapisywalny: bez tego nie ma logu, blokady ani STOP."""
    probe = APP_DIR / ".write-test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def main() -> None:
    sys.excepthook = _report_crash
    if not state_dir_writable():
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(t("crash.title"), t("state.not_writable", path=APP_DIR))
        root.destroy()
        return
    other = another_instance_running()
    if other is not None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showwarning(t("instance.title"), t("instance.body", pid=other))
        root.destroy()
        return
    claim_instance_lock()
    try:
        ClaudeAutoShutdown().run()
    finally:
        LOCK_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
