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
APP_DIR = Path(os.environ.get("CLAUDE_AUTOSHUTDOWN_HOME")
               or Path(__file__).resolve().parent)
APP_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = APP_DIR / "config.json"
LOG_PATH = APP_DIR / "autoshutdown.log"
STOP_FILE = APP_DIR / "STOP"

# Tryb testu koncowego: uzbraja bez okienka potwierdzenia. Wlaczany WYLACZNIE przez
# zmienna srodowiskowa i glosno logowany - normalne uruchomienie go nie widzi.
AUTOARM = os.environ.get("CLAUDE_AUTOSHUTDOWN_AUTOARM") == "1"

LOCK_FILE = APP_DIR / "instance.lock"
LOG_MAX_BYTES = 5 * 1024 * 1024

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


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    # UWAGA: tryb prob DZIEDZICZY sie z pliku - jesli uzytkownik swiadomie go zdjal,
    # ma zostac zdjety. Tym, czego nigdy nie dziedziczymy, jest UZBROJENIE:
    # program zawsze startuje rozbrojony (ClaudeAutoShutdown.armed = False).
    cfg["dry_run"] = bool(cfg.get("dry_run", True))
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


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
        verdict = evaluate(
            sessions,
            quiet_seconds=float(cfg["quiet_seconds"]),
            armed=self.app.armed,
            stop_file_present=STOP_FILE.exists(),
            human_idle=winprobe.human_idle_seconds(),
            require_human_idle=bool(cfg["require_human_idle"]),
            human_idle_required=float(cfg["human_idle_required"]),
            allow_zero_sessions=bool(cfg["allow_zero_sessions"]),
            saw_any_session=self.saw_any_session,
            guard_patterns=patterns,
            guard_hits=matching_guard_processes(patterns),
            stable_polls=self.stable_polls,
            required_polls=int(cfg["required_polls"]),
            scan_error=self.scanner.last_error,
            seconds_since_last_session=(time.time() - self.last_session_seen
                                        if self.last_session_seen else 0.0),
            unregistered_pids=(
                claude_processes_without_registry({s.pid for s in sessions})
                if self.scanner.is_default_root else []),
        )
        self.stable_polls = verdict.stable_polls
        self.results.put(("snapshot", (sessions, verdict, self.scanner.last_error)))


# --------------------------------------------------------------------------- #
# Okno odliczania
# --------------------------------------------------------------------------- #
class CountdownWindow(tk.Toplevel):
    """Ostatnia bramka przed akcja. Zamkniecie okna = anulowanie."""

    def __init__(self, app: ClaudeAutoShutdown, seconds: int, action_label: str) -> None:
        super().__init__(app.root)
        self.app = app
        self.remaining = seconds
        self.cancelled = False

        self.title("Claude AutoShutdown - odliczanie")
        self.configure(bg=BG)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW",
                      lambda: self.cancel("zamkniecie okna odliczania"))
        self.bind("<Escape>", lambda _e: self.cancel("klawisz Esc"))
        self.geometry(f"{app.px(600)}x{app.px(330)}")

        tk.Label(self, text="Wszystkie sesje skonczyly prace",
                 bg=BG, fg=FG, font=("Segoe UI", 16, "bold")).pack(pady=(24, 4))
        tk.Label(self, text=action_label, bg=BG, fg=WARN_COLOR,
                 font=("Segoe UI", 12)).pack()

        self.counter = tk.Label(self, text=str(seconds), bg=BG, fg=BAD_COLOR,
                                font=("Segoe UI", 72, "bold"))
        self.counter.pack(pady=6)

        self.note = tk.Label(self, text="Ruch mysza lub nowa aktywnosc sesji tez przerwie akcje",
                             bg=BG, fg=FG_DIM, font=("Segoe UI", 9))
        self.note.pack()

        row = tk.Frame(self, bg=BG)
        row.pack(pady=16)
        self.cancel_button = tk.Button(
            row, text="ANULUJ  (Esc)", command=lambda: self.cancel("przycisk ANULUJ"),
            bg=OK_COLOR, fg="#0b0f14", font=("Segoe UI", 13, "bold"), width=16,
            relief="flat", cursor="hand2")
        self.cancel_button.pack(side="left", padx=8)
        # "Wykonaj teraz" celowo nie przyjmuje focusu klawiatury - przypadkowa spacja
        # ma anulowac, nigdy przyspieszyc wylaczenie.
        tk.Button(row, text="Wykonaj teraz", command=self.fire, bg=BG_ROW, fg=FG,
                  font=("Segoe UI", 10), width=14, relief="flat", takefocus=0,
                  cursor="hand2").pack(side="left", padx=8)

        self.lift()
        self.cancel_button.focus_set()
        self._tick()

    def _tick(self) -> None:
        if self.cancelled:
            return
        if self.remaining <= 0:
            self.fire()
            return
        self.counter.config(text=str(self.remaining))
        try:
            self.bell()
        except tk.TclError:
            pass
        self.remaining -= 1
        self.after(1000, self._tick)

    def cancel(self, reason: str = "zrodlo nieznane") -> None:
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
        self.app.execute_action()


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
        self.log_lines: list[str] = []
        self.selected_session_id: str | None = None

        enable_dpi_awareness()
        self.root = tk.Tk()
        # Wszystkie rozmiary podajemy logicznie i mnozymy przez skale monitora.
        self.dpi = self.root.winfo_fpixels("1i") / 96.0
        self.root.title("Claude AutoShutdown")
        self.root.geometry(f"{self.px(1400)}x{self.px(820)}")
        self.root.minsize(self.px(1000), self.px(620))
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_style()
        self._build_header()
        self._build_body()
        # Wpis MUSI opisywac rzeczywisty stan - staly napis "tryb prob" klamalby
        # dokladnie wtedy, gdy program potrafi naprawde wylaczyc komputer.
        tryb = "tryb prob" if self.cfg["dry_run"] else "TRYB BOJOWY"
        akcja = winprobe.POWER_ACTIONS[self.cfg["action"]].label
        self._append_log(log_line(
            f"Start programu (ROZBROJONY, {tryb}, akcja: {akcja}, "
            f"cisza {self.cfg['quiet_seconds']}s, bezczynnosc "
            f"{self.cfg['human_idle_required']}s)"))

        if AUTOARM:
            self.armed = True
            self._append_log(log_line(
                "AUTOARM: uzbrojono bez potwierdzenia (CLAUDE_AUTOSHUTDOWN_AUTOARM=1) "
                "- tryb testu koncowego"))
            self._render_header()
        elif self.cfg.get("arm_on_start"):
            # Swiadome oslabienie bezpiecznika nr 1: program uzbraja sie sam po
            # starcie, zeby po restarcie komputera nie trzeba bylo nic klikac.
            # Reszta bramek (cisza, tury, bezczynnosc, odliczanie, STOP) zostaje.
            self.armed = True
            self._append_log(log_line(
                f"Uzbrojono automatycznie przy starcie (arm_on_start=true) - "
                f"akcja: {winprobe.POWER_ACTIONS[self.cfg['action']].label}, "
                f"tryb prob: {self.cfg['dry_run']}"))
            self._render_header()

        self.monitor = MonitorThread(self)
        self.monitor.start()
        self.root.after(300, self._drain_queue)
        self.root.after(1000, self._tick_preview)
        self.root.after(5000, self._check_monitor_alive)

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
        self.arm_button = tk.Button(right, text="UZBROJ", command=self.toggle_arm,
                                    bg=OK_COLOR, fg="#0b0f14", width=13, relief="flat",
                                    font=("Segoe UI", 12, "bold"), cursor="hand2")
        self.arm_button.pack(side="right", padx=(10, 0), pady=18)
        self.counts_label = tk.Label(right, text="", bg=BG_PANEL, fg=FG,
                                     font=("Segoe UI", 10))
        self.counts_label.pack(side="right", padx=12)

        left = tk.Frame(head, bg=BG_PANEL)
        left.pack(side="left", padx=18, pady=12, fill="both", expand=True)

        self.state_label = tk.Label(left, text="● ROZBROJONY", bg=BG_PANEL, fg=FG_DIM,
                                    font=("Segoe UI", 15, "bold"))
        self.state_label.pack(anchor="w")
        self.verdict_label = tk.Label(left, text="Uruchamiam skaner...", bg=BG_PANEL,
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

        for key, label in (("monitor", "Monitor"), ("preview", "Podglad"),
                           ("settings", "Ustawienia"), ("log", "Log")):
            btn = tk.Button(nav, text=label, command=lambda k=key: self.show_page(k),
                            bg=BG_PANEL, fg=FG_DIM, relief="flat", anchor="w",
                            font=("Segoe UI", 11), padx=20, pady=12, cursor="hand2",
                            activebackground=BG_ROW, activeforeground=FG)
            btn.pack(fill="x")
            self.nav_buttons[key] = btn
            page = tk.Frame(self.content, bg=BG)
            self.pages[key] = page

        tk.Label(nav, text="STOP = plik w katalogu\nprogramu blokuje akcje",
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
        tk.Label(page, text="Zywe sesje Claude", bg=BG, fg=FG,
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=18, pady=(16, 6))

        columns = ("state", "name", "where", "why", "silence", "cpu", "subs", "pid")
        wrap = tk.Frame(page, bg=BG)
        wrap.pack(fill="x", padx=18)
        self.tree = ttk.Treeview(wrap, columns=columns, show="headings", height=9)
        headings = {
            "state": ("Stan", 95), "name": ("Sesja", 125), "where": ("Katalog / typ", 180),
            "why": ("Dlaczego", 250), "silence": ("Cisza", 80), "cpu": ("CPU", 60),
            "subs": ("Subagenci", 90), "pid": ("PID", 60),
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

        tk.Label(page, text="Warunki wylaczenia (wszystkie musza byc zielone)", bg=BG,
                 fg=FG, font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=18,
                                                            pady=(18, 6))
        self.checks_frame = tk.Frame(page, bg=BG_PANEL)
        self.checks_frame.pack(fill="both", expand=True, padx=18, pady=(0, 18))

    def _on_tree_select(self, _event: object) -> None:
        selection = self.tree.selection()
        if selection:
            self.selected_session_id = selection[0]
            self.preview_combo.set(self._combo_label_for(selection[0]))

    # --- strona: podglad ---------------------------------------------------
    def _build_preview_page(self, page: tk.Frame) -> None:
        top = tk.Frame(page, bg=BG)
        top.pack(fill="x", padx=18, pady=(16, 8))
        tk.Label(top, text="Sesja:", bg=BG, fg=FG_DIM,
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
        for session in self.sessions:
            if self._combo_label_for(session.session_id) == label:
                self.selected_session_id = session.session_id
                break
        self._refresh_preview(force=True)

    def _combo_label_for(self, session_id: str) -> str:
        for session in self.sessions:
            if session.session_id == session_id:
                return f"{session.name} - {session.short_cwd} ({session.surface}, PID {session.pid})"
        return session_id[:12]

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

        add_row(0, "quiet_seconds", "Cisza sesji [s]",
                "ile sekund bez zapisu do transkryptu = sesja skonczyla")
        add_row(1, "poll_seconds", "Co ile sprawdzac [s]", "czestotliwosc skanu")
        add_row(2, "required_polls", "Potwierdzen z rzedu",
                "tyle cykli pod rzad musi byc czysto")
        add_row(3, "countdown_seconds", "Odliczanie [s]", "czas na anulowanie")
        add_row(4, "human_idle_required", "Bezczynnosc uzytkownika [s]",
                "ile nie ruszasz myszy zanim wolno wylaczyc")

        row = 5
        tk.Label(wrap, text="Akcja", bg=BG, fg=FG,
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
            (self.dry_var, "Tryb prob (nic nie wylacza, tylko loguje)", "zdejmij gdy ufasz"),
            (self.human_var, "Wymagaj bezczynnosci uzytkownika", "chroni gdy siedzisz przy kompie"),
            (self.zero_var, "Pozwol dzialac gdy zero sesji od startu", "domyslnie wylaczone"),
            (self.armstart_var, "Uzbrajaj sie sam przy starcie",
             "zero klikania po restarcie - reszta bramek dziala normalnie"),
            (self.force_var, "Wymus zamkniecie aplikacji (/f)",
             "bez tego Windows moze pokazac ekran blokujacy i czekac na klikniecie"),
        ):
            tk.Checkbutton(wrap, text=text, variable=var, bg=BG, fg=FG, selectcolor=BG_ROW,
                           activebackground=BG, activeforeground=FG, relief="flat",
                           font=("Segoe UI", 10)).grid(row=row, column=0, columnspan=2,
                                                       sticky="w", pady=3)
            tk.Label(wrap, text=hint, bg=BG, fg=FG_DIM,
                     font=("Segoe UI", 9)).grid(row=row, column=2, sticky="w")
            row += 1

        tk.Label(wrap, text="Procesy-straznicy (po przecinku)", bg=BG, fg=FG,
                 font=("Segoe UI", 10)).grid(row=row, column=0, sticky="w", pady=6)
        self.guard_var = tk.StringVar(value=", ".join(self.cfg.get("guard_patterns") or []))
        tk.Entry(wrap, textvariable=self.guard_var, width=40, bg=BG_ROW, fg=FG,
                 relief="flat", insertbackground=FG).grid(row=row, column=1, columnspan=2,
                                                          sticky="w", padx=10)
        row += 1
        tk.Label(wrap, text="np. ffmpeg, handbrake - dopoki zyja, nie wylaczamy",
                 bg=BG, fg=FG_DIM, font=("Segoe UI", 9)).grid(row=row, column=1,
                                                              columnspan=2, sticky="w",
                                                              padx=10)
        row += 1
        tk.Button(wrap, text="Zapisz ustawienia", command=self.save_settings, bg=ACCENT,
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
        tk.Label(wrap, text="Uprawnienia", bg=BG, fg=FG,
                 font=("Segoe UI", 10, "bold")).grid(row=row, column=0, sticky="w",
                                                     pady=(10, 2))
        row += 1
        tk.Label(wrap, text=("OK  " if can_shutdown else "BRAK  ") + why, bg=BG,
                 fg=OK_COLOR if can_shutdown else BAD_COLOR,
                 font=("Segoe UI", 9)).grid(row=row, column=0, columnspan=3, sticky="w")
        if states:
            row += 1
            tk.Label(wrap, text=f"Stany zasilania wspierane przez system: {states}",
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
            messagebox.showerror("Blad", "Liczby, prosze. Sprawdz pola.")
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
        try:
            save_config(self.cfg)
        except OSError as exc:
            messagebox.showerror("Blad zapisu", str(exc))
            return
        self.monitor.reset_stability()
        self.monitor.wake()
        self.settings_status.config(text="zapisane")
        self.root.after(2500, lambda: self.settings_status.config(text=""))
        self._append_log(log_line(f"Ustawienia zapisane: {json.dumps(self.cfg, ensure_ascii=False)}"))

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

    # --- uzbrajanie --------------------------------------------------------
    def toggle_arm(self) -> None:
        if self.armed:
            self.armed = False
            self.monitor.reset_stability()
            self._append_log(log_line("ROZBROJONY (recznie)"))
            # Rozbrojenie MUSI zatrzymac trwajace odliczanie - inaczej przycisk
            # klamie: napis zmienia sie na ROZBROJONY, a komputer i tak gasnie.
            if self.countdown is not None:
                self.countdown.cancel("rozbrojenie w trakcie odliczania")
            self._render_header()
            return

        action_name = self.cfg["action"]
        action = winprobe.POWER_ACTIONS[action_name]
        label = action.label
        if action.needs_privilege:
            can, why = winprobe.shutdown_capability()
            if not can:
                self._append_log(log_line(f"Odmowa uzbrojenia - brak uprawnien: {why}"))
                messagebox.showerror(
                    "Nie moge uzbroic",
                    f"Akcja '{label}' wymaga przywileju wylaczania, a system go "
                    f"odmawia:\n\n{why}\n\nWybierz inna akcje w Ustawieniach.")
                return
        mode = "TRYB PROB - nic sie nie stanie" if self.cfg["dry_run"] else "NAPRAWDE WYKONA AKCJE"
        summary = (
            f"Akcja: {label}\n"
            f"Tryb: {mode}\n\n"
            f"Warunki:\n"
            f"  - kazda sesja cicho przez {self.cfg['quiet_seconds']} s\n"
            f"  - zero pracujacych subagentow\n"
            f"  - kazda tura domknieta (end_turn), zadna nieznana\n"
            f"  - {self.cfg['required_polls']} potwierdzenia z rzedu\n"
            + (f"  - brak ruchu myszy przez {self.cfg['human_idle_required']} s\n"
               if self.cfg["require_human_idle"] else "")
            + f"  - odliczanie {self.cfg['countdown_seconds']} s z przyciskiem ANULUJ\n\n"
            + ("Aplikacje zostana zamkniete WYMUSZONE (/f): Windows nie zapyta o zgode,\n"
               "ale niezapisana praca w innych programach przepadnie.\n\n"
               if action_name == "shutdown" and self.cfg.get("force_close_apps", True)
               else "")
            + "Uzbroic?"
        )
        if not messagebox.askyesno("Potwierdz uzbrojenie", summary, icon="warning"):
            return
        self.armed = True
        self.monitor.reset_stability()
        self.monitor.wake()
        self._append_log(log_line(f"UZBROJONY - akcja={action_name}, dry_run={self.cfg['dry_run']}"))
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
                    if scan_error:
                        self._append_log(log_line(f"Uwaga skanera: {scan_error}"))
                    self._render_all()
                    self._log_blocker_change(verdict)
                    self._maybe_trigger(verdict, snapshot_at)
                elif kind == "error":
                    self._append_log(log_line(f"Blad watku monitora: {payload.splitlines()[-1]}"))
        except queue.Empty:
            pass
        except Exception:  # noqa: BLE001 - petla pilnujaca nie moze umrzec po cichu
            try:
                self._append_log(log_line(
                    f"Blad petli GUI: {traceback.format_exc().splitlines()[-1]}"))
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
        stale_after = max(30.0, 3 * float(self.cfg["poll_seconds"]))
        age = time.time() - self.last_snapshot
        if age > stale_after and self.monitor.is_alive():
            self.state_label.config(text="● MONITOR MILCZY", fg=BAD_COLOR)
            self.verdict_label.config(
                text=f"Brak swiezych danych od {fmt_duration(age)} - nie ufaj temu widokowi",
                fg=BAD_COLOR)
        elif not self.monitor.is_alive():
            self.state_label.config(text="● MONITOR PADL", fg=BAD_COLOR)
            self.verdict_label.config(text="Watek skanera nie zyje - uruchom program ponownie",
                                      fg=BAD_COLOR)
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
            self._append_log(log_line("Czekam, bo: " + " | ".join(b[:120] for b in current)))
        else:
            self._append_log(log_line("Wszystkie warunki zielone"))

    def _maybe_trigger(self, verdict: Verdict, snapshot_at: float = 0.0) -> None:
        if self.countdown is not None:
            # Odliczanie trwa - jesli cokolwiek przestalo byc czyste, przerywamy.
            if not all(passed for name, passed, _ in verdict.checks
                       if not name.startswith("Potwierdzone")):
                blockers = "; ".join(verdict.blockers)
                self.countdown.cancel(f"warunki przestaly byc spelnione ({blockers})")
            return
        if verdict.ok and snapshot_at and snapshot_at <= self.last_cancel:
            # Migawka powstala PRZED anulowaniem - gdyby ja uwzglednic, ANULUJ
            # natychmiast otwieralby kolejne okno odliczania.
            return
        if verdict.ok and self.armed:
            label = winprobe.POWER_ACTIONS[self.cfg["action"]].label
            if self.cfg["dry_run"]:
                label += "  [TRYB PROB]"
            self._append_log(log_line(
                f"Warunki spelnione {verdict.stable_polls}/{verdict.required_polls} "
                f"- start odliczania {self.cfg['countdown_seconds']} s"))
            self.countdown = CountdownWindow(self, int(self.cfg["countdown_seconds"]), label)

    def on_countdown_cancelled(self, reason: str) -> None:
        self.countdown = None
        self.last_cancel = time.time()
        self.monitor.reset_stability()
        self._append_log(log_line(f"Odliczanie ANULOWANE: {reason}"))
        self._render_header()

    def execute_action(self) -> None:
        self.countdown = None
        # Ostatnia bramka tuz przed akcja. Miedzy startem odliczania a ta chwila
        # uzytkownik mogl rozbroic program albo postawic plik STOP.
        if not self.armed:
            self._append_log(log_line("Akcja pominieta - program jest rozbrojony"))
            return
        if STOP_FILE.exists():
            self._append_log(log_line("Akcja pominieta - pojawil sie plik STOP"))
            self.armed = False
            self._render_header()
            return
        # Odliczanie leci na zegarze Tk, niezaleznie od skanera. Gdyby skaner umarl
        # tuz po starcie odliczania, nikt nie zauwazylby, ze sesja wrocila do pracy.
        snapshot_age = time.time() - self.last_snapshot
        max_age = max(30.0, 3 * float(self.cfg["poll_seconds"]))
        if snapshot_age > max_age or not self.monitor.is_alive():
            self._append_log(log_line(
                f"Akcja WSTRZYMANA - dane skanera nieswieze ({fmt_duration(snapshot_age)}), "
                f"watek zyje: {self.monitor.is_alive()}"))
            self.armed = False
            self._render_header()
            messagebox.showwarning(
                "Akcja wstrzymana",
                "Skaner przestal dostarczac dane, wiec nie wiem, czy sesje nadal "
                "pracuja.\n\nProgram sie rozbroil zamiast zgadywac.")
            return
        action = self.cfg["action"]
        if self.cfg["dry_run"]:
            self._append_log(log_line(
                f"TRYB PROB: tutaj poszloby '{winprobe.POWER_ACTIONS[action].label}'. "
                "Odznacz 'Tryb prob' w Ustawieniach, zeby dzialalo naprawde."))
            self.armed = False
            self.monitor.reset_stability()
            self._render_header()
            messagebox.showinfo("Tryb prob",
                                "Warunki spelnione - w trybie bojowym komputer zostalby "
                                f"teraz obsluzony akcja: {winprobe.POWER_ACTIONS[action].label}.")
            return
        ok, detail = winprobe.power_action(
            action, force=bool(self.cfg.get("force_close_apps", True)))
        if ok:
            self._append_log(log_line(f"AKCJA WYKONANA: {detail}"))
            return
        # Cicha porazka byla by najgorsza: uzytkownik mysli ze komputer zgasl,
        # a rano zastaje go wlaczonego bez sladu dlaczego.
        self._append_log(log_line(f"AKCJA NIEUDANA: {detail}"))
        self.armed = False
        self.monitor.reset_stability()
        self._render_header()
        messagebox.showerror(
            "Akcja nie powiodla sie",
            f"{detail}\n\nProgram sie rozbroil. Szczegoly w zakladce Log.")

    # --- rendering ---------------------------------------------------------
    def _render_all(self) -> None:
        self._render_header()
        self._render_tree()
        self._render_checks()
        self._render_combo()

    def _render_header(self) -> None:
        if self.armed:
            self.state_label.config(text="● UZBROJONY", fg=OK_COLOR)
            self.arm_button.config(text="ROZBRÓJ", bg=BAD_COLOR, fg="#ffffff")
        else:
            self.state_label.config(text="● ROZBROJONY", fg=FG_DIM)
            self.arm_button.config(text="UZBROJ", bg=OK_COLOR, fg="#0b0f14")

        working = sum(1 for s in self.sessions if s.working)
        mode = " · tryb prob" if self.cfg["dry_run"] else " · TRYB BOJOWY"
        self.counts_label.config(
            text=f"sesje: {len(self.sessions)}  ·  pracuja: {working}{mode}")

        if self.verdict is None:
            return
        if self.verdict.ok:
            text, color = "Warunki spelnione - odliczanie", OK_COLOR
        else:
            blockers = [b for b in self.verdict.blockers
                        if not b.startswith("Uzbrojony")] or self.verdict.blockers
            first = blockers[0] if blockers else "?"
            if len(first) > 96:
                first = first[:93] + "..."
            text = "Czekam - " + first
            if len(blockers) > 1:
                text += f"  (+{len(blockers) - 1} innych)"
            color = WARN_COLOR if self.armed else FG_DIM
        self.verdict_label.config(text=text, fg=color)

    def _render_tree(self) -> None:
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for session in self.sessions:
            subs = f"{session.active_subagents} aktywnych" if session.active_subagents else "-"
            self.tree.insert(
                "", "end", iid=session.session_id,
                values=(session.state, session.name,
                        f"{session.short_cwd}  ({session.surface})", session.why,
                        fmt_duration(session.silence), f"{session.cpu_percent:.1f}%",
                        subs, session.pid),
                tags=("working" if session.working else "idle",))
        for iid in selected:
            if self.tree.exists(iid):
                self.tree.selection_add(iid)

    def _render_checks(self) -> None:
        for child in self.checks_frame.winfo_children():
            child.destroy()
        if self.verdict is None:
            return
        for name, passed, detail in self.verdict.checks:
            row = tk.Frame(self.checks_frame, bg=BG_PANEL)
            row.pack(fill="x", padx=14, pady=3)
            tk.Label(row, text="OK" if passed else "NIE", width=4,
                     bg=BG_PANEL, fg=OK_COLOR if passed else BAD_COLOR,
                     font=("Segoe UI", 9, "bold")).pack(side="left")
            tk.Label(row, text=name, bg=BG_PANEL, fg=FG, width=34, anchor="w",
                     font=("Segoe UI", 10)).pack(side="left")
            tk.Label(row, text=detail, bg=BG_PANEL, fg=FG_DIM, anchor="w",
                     font=("Segoe UI", 9)).pack(side="left", fill="x", expand=True)

    def _render_combo(self) -> None:
        labels = [self._combo_label_for(s.session_id) for s in self.sessions]
        self.preview_combo.configure(values=labels)
        if self.selected_session_id is None and self.sessions:
            self.selected_session_id = self.sessions[0].session_id
        if self.selected_session_id and not self.preview_combo.get():
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
        if session is None or session.transcript is None:
            if force:
                self._set_preview_text("Brak transkryptu dla tej sesji.")
            return

        events = tail_events(session.transcript, count=45)
        lines: list[tuple[str, str, str]] = [describe_event(e) for e in events]
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        for ts, who, text in lines:
            self.preview_text.insert("end", f"{ts} ", "time")
            self.preview_text.insert("end", f"{who}: ", "who")
            self.preview_text.insert("end", text + "\n",
                                     "tool" if text.startswith(("->", "<-")) else "")
        self.preview_text.see("end")
        self.preview_text.configure(state="disabled")

        age = time.time() - session.last_activity
        self.preview_status.config(
            text=f"{session.state} · ostatni zapis {fmt_duration(age)} temu · "
                 f"{session.transcript.name}")

    def _set_preview_text(self, text: str) -> None:
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("end", text)
        self.preview_text.configure(state="disabled")

    # --- zamkniecie --------------------------------------------------------
    def on_close(self) -> None:
        if self.armed and not messagebox.askyesno(
                "Zamknac?", "Program jest UZBROJONY. Zamkniecie anuluje pilnowanie. Zamknac?"):
            return
        self._append_log(log_line("Zamkniecie programu"))
        self.closing = True
        self.monitor.stop()
        LOCK_FILE.unlink(missing_ok=True)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    other = another_instance_running()
    if other is not None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showwarning(
            "Program juz dziala",
            f"Claude AutoShutdown dziala juz w procesie {other}.\n\n"
            "Dwie instancje moglyby wykonac akcje dwa razy, wiec ta sie zamyka.")
        root.destroy()
        return
    claim_instance_lock()
    try:
        ClaudeAutoShutdown().run()
    finally:
        LOCK_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
