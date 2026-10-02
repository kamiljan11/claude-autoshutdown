"""Backend Linux - ten sam kontrakt co winprobe, zero zaleznosci zewnetrznych.

Daje monitorowi to samo co winprobe:
  * probe_process(pid)   -> /proc/<pid>/stat + /proc/<pid>/exe
  * human_idle_seconds() -> GNOME Mutter IdleMonitor (Wayland i X11), fallback xprintidle
  * power_action(name)   -> systemd-logind przez `systemctl` / `loginctl`

Jednostki (ProcInfo zachowuje nazwy pol z Windows, zeby silnik byl jeden):
  * created_filetime = czas startu procesu w TYKACH ZEGARA od startu systemu
    (pole 22 z /proc/<pid>/stat). Dokladnie te wartosc Claude Code zapisuje na
    Linuksie w `procStart` rejestru sesji - zweryfikowane: 633076 == 633076.
    PROC_START_UNITS_PER_SECOND mowi silnikowi, ile tych jednostek to sekunda.
  * cpu_100ns = utime + stime przeliczone na 100 ns, czyli ta sama jednostka co
    na Windows - silnik dzieli ja przez FILETIME_PER_SECOND bez zadnych wyjatkow.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from i18n import t

IS_LINUX = sys.platform.startswith("linux")

FILETIME_PER_SECOND = 10_000_000  # jednostka cpu_100ns - wspolna z Windows
try:
    CLOCK_TICKS = os.sysconf("SC_CLK_TCK") if IS_LINUX else 100
except (ValueError, OSError, AttributeError):
    CLOCK_TICKS = 100
PROC_START_UNITS_PER_SECOND = CLOCK_TICKS

PROC_ROOT = Path("/proc")
QUERY_TIMEOUT_SECONDS = 5
ACTION_TIMEOUT_SECONDS = 25


@dataclass(frozen=True)
class ProcInfo:
    """Migawka procesu. alive=False => reszta pol jest bez znaczenia."""
    pid: int
    alive: bool
    exe: str = ""
    created_filetime: int = 0  # na Linuksie: tyki zegara od startu systemu
    cpu_100ns: int = 0

    @property
    def name(self) -> str:
        return os.path.basename(self.exe)


DEAD = ProcInfo(pid=0, alive=False)


def parse_stat(raw: str) -> tuple[str, int, int] | None:
    """(comm, starttime, utime+stime) z linii /proc/<pid>/stat albo None.

    comm stoi w nawiasach i MOZE zawierac spacje oraz nawiasy ("(sd-pam)",
    "Web Content"), wiec dzielimy po OSTATNIM ')' - split() po spacjach
    przesuwalby numery pol i czytal smieci jako czas startu.
    """
    head, sep, tail = raw.rpartition(")")
    if not sep or "(" not in head:
        return None
    comm = head.split("(", 1)[1]
    fields = tail.split()
    # tail zaczyna sie od pola 3 (state), wiec pole N ma indeks N - 3.
    try:
        utime, stime = int(fields[11]), int(fields[12])
        starttime = int(fields[19])
    except (IndexError, ValueError):
        return None
    return comm, starttime, utime + stime


def probe_process(pid: int) -> ProcInfo:
    """Czy PID zyje, jaki exe, kiedy wystartowal (tyki), ile zjadl CPU."""
    if not IS_LINUX or pid <= 0:
        return ProcInfo(pid=pid, alive=False)
    base = PROC_ROOT / str(pid)
    try:
        parsed = parse_stat((base / "stat").read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ProcInfo(pid=pid, alive=False)
    if parsed is None:
        return ProcInfo(pid=pid, alive=False)
    comm, starttime, cpu_ticks = parsed
    try:
        exe = os.readlink(base / "exe")
    except OSError:
        exe = comm  # proces innego uzytkownika / watek jadra - nazwa z comm
    exe = exe.removesuffix(" (deleted)")  # binarka podmieniona przez aktualizacje
    return ProcInfo(
        pid=pid,
        alive=True,
        exe=exe,
        created_filetime=starttime,
        cpu_100ns=cpu_ticks * FILETIME_PER_SECOND // CLOCK_TICKS,
    )


def boot_id() -> str:
    """Identyfikator biezacego startu systemu. Czas startu procesu jest liczony od
    bootu, wiec bez tego blokada sprzed restartu mogla trafic ten sam PID i te same
    tyki co nowy proces i udawac zywa instancje."""
    try:
        return (PROC_ROOT / "sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except OSError:
        return ""


def _all_pids() -> list[int] | None:
    try:
        return [int(entry) for entry in os.listdir(PROC_ROOT) if entry.isdigit()]
    except OSError:
        return None


def process_names() -> set[str] | None:
    """Nazwy wszystkich procesow (male litery). None = nie da sie odczytac /proc.

    comm jest obciety do 15 znakow, wiec dokladamy tez nazwe z argv[0] -
    inaczej straznik `blender-softwaregl` nigdy by nie trafil.
    """
    pids = _all_pids()
    if not pids:
        return None
    names: set[str] = set()
    for pid in pids:
        base = PROC_ROOT / str(pid)
        try:
            names.add((base / "comm").read_text(encoding="utf-8", errors="replace")
                      .strip().lower())
            argv0 = (base / "cmdline").read_bytes().split(b"\0", 1)[0]
        except OSError:
            continue  # proces zniknal w trakcie skanu
        if argv0:
            names.add(os.path.basename(argv0.decode("utf-8", "replace")).lower())
    names.discard("")
    return names or None


# Sesja Claude Code to natywna binarka `claude` z katalogu wersji:
#   ~/.local/share/claude/versions/<wersja>            (CLI, instalator natywny)
#   ~/.config/Claude/claude-code/<wersja>/claude       (aplikacja desktopowa)
# Sama nazwa `claude` nie wystarczy - aplikacja desktopowa (Electron) ma tez
# procesy pomocnicze o podobnych nazwach.
_CLAUDE_CODE_MARKERS = ("/claude-code/", "/claude/versions/")


def claude_code_pids() -> list[int]:
    """PID-y zywych sesji Claude Code wedlug systemu (nie wedlug rejestru)."""
    found: list[int] = []
    for pid in _all_pids() or []:
        exe = probe_process(pid).exe.lower()
        if any(marker in exe for marker in _CLAUDE_CODE_MARKERS):
            found.append(pid)
    return sorted(found)


# --- bezczynnosc czlowieka ----------------------------------------------------
_MUTTER_IDLE = ["gdbus", "call", "--session", "--dest", "org.gnome.Mutter.IdleMonitor",
                "--object-path", "/org/gnome/Mutter/IdleMonitor/Core",
                "--method", "org.gnome.Mutter.IdleMonitor.GetIdletime"]


def _query(args: list[str]) -> str | None:
    """stdout komendy albo None przy dowolnej awarii (brak binarki, timeout, kod != 0)."""
    if shutil.which(args[0]) is None:
        return None
    try:
        done = subprocess.run(args, capture_output=True, text=True, check=False,
                              timeout=QUERY_TIMEOUT_SECONDS, encoding="utf-8",
                              errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def parse_mutter_idle(out: str) -> float | None:
    """'(uint64 66615,)' -> 66.615 s."""
    digits = "".join(ch for ch in out.split("uint64", 1)[-1] if ch.isdigit())
    return int(digits) / 1000.0 if digits else None


def human_idle_seconds() -> float:
    """Ile sekund od ostatniego ruchu myszy / klawisza. -1 gdy nieznane.

    Wayland nie daje aplikacjom globalnego odczytu wejscia, wiec pytamy
    kompozytor (GNOME Mutter). Na X11 bez GNOME zostaje xprintidle.
    """
    if not IS_LINUX:
        return -1.0
    out = _query(_MUTTER_IDLE)
    if out is not None:
        value = parse_mutter_idle(out)
        if value is not None:
            return value
    out = _query(["xprintidle"])
    if out is not None and out.strip().isdigit():
        return int(out.strip()) / 1000.0
    return -1.0


# --- akcje zasilania ----------------------------------------------------------
@dataclass(frozen=True)
class PowerAction:
    """Opis akcji zasilania. logind_can = metoda Can* w org.freedesktop.login1."""
    key: str
    label_key: str
    args: list[str] | None
    wait_for_exit: bool = True
    needs_privilege: bool = True
    logind_can: str = ""

    @property
    def label(self) -> str:
        return t(self.label_key)


# -i (= --check-inhibitors=no, udokumentowane) = odpowiednik /f z Windows: bez tego otwarta aplikacja
# z blokada (niezapisany dokument, odtwarzacz) zatrzymuje wylaczenie i logind
# CZEKA - czyli komputer zostaje wlaczony na cala noc.
POWER_ACTIONS: dict[str, PowerAction] = {
    "shutdown": PowerAction("shutdown", "action.shutdown",
                            ["systemctl", "poweroff", "-i"],
                            logind_can="CanPowerOff"),
    "hibernate": PowerAction("hibernate", "action.hibernate",
                             ["systemctl", "hibernate"], logind_can="CanHibernate"),
    "sleep": PowerAction("sleep", "action.sleep",
                         ["systemctl", "suspend"], logind_can="CanSuspend"),
    # lock-sessions (liczba mnoga) wymaga hasla admina (polkit auth_admin_keep).
    # lock-session auto = sesja graficzna TEGO uzytkownika - wlasciciel moze bez hasla,
    # a "auto" dziala tez z procesu spoza scope sesji (start ze skrotu GNOME).
    "lock": PowerAction("lock", "action.lock", ["loginctl", "lock-session", "auto"],
                        needs_privilege=False),
    "nothing": PowerAction("nothing", "action.nothing", None, needs_privilege=False),
}

_SHUTDOWN_POLITE = ["systemctl", "poweroff"]


def power_action(name: str, force: bool = True) -> tuple[bool, str]:
    """Wykonuje akcje zasilania. Zwraca (czy sie powiodlo, opis dla logu).

    Kod wyjscia jest SPRAWDZANY: systemctl odmawia (polkit, blokada, brak swapu
    dla hibernacji) kodem != 0 i to musi byc porazka, nie cichy "sukces".
    """
    action = POWER_ACTIONS.get(name)
    if action is None:
        return False, t("power.unknown_action", name=name)
    if action.args is None:
        return True, t("power.nothing_done", label=action.label)
    if not IS_LINUX:
        return False, t("power.skipped_unsupported", label=action.label)

    args = list(action.args)
    if name == "shutdown" and not force:
        args = list(_SHUTDOWN_POLITE)
    printable = " ".join(args)
    try:
        done = subprocess.run(args, capture_output=True, text=True, check=False,
                              encoding="utf-8", errors="replace",
                              timeout=ACTION_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return False, t("power.timeout", label=action.label, cmd=printable,
                        seconds=ACTION_TIMEOUT_SECONDS)
    except OSError as exc:
        return False, t("power.cannot_start", label=action.label, error=exc)

    if done.returncode == 0:
        return True, t("power.done", label=action.label, cmd=printable)
    detail = (done.stderr or done.stdout or "").strip().splitlines()
    reason = detail[0] if detail else "-"
    return False, t("power.failed", label=action.label, code=done.returncode,
                    reason=reason, cmd=printable)


def cancel_pending_shutdown() -> None:
    """Anuluje zaplanowane `shutdown +N` (gdy ktos ustawi opoznienie)."""
    if IS_LINUX and shutil.which("shutdown"):
        subprocess.run(["shutdown", "-c"], capture_output=True, check=False)


def logind_can(method: str) -> str:
    """Odpowiedz logind na Can*: yes / no / challenge / na, albo '' gdy nieznana."""
    out = _query(["busctl", "call", "org.freedesktop.login1", "/org/freedesktop/login1",
                  "org.freedesktop.login1.Manager", method])
    if out is None:
        return ""
    # busctl: 's "yes"'
    return out.strip().split(" ", 1)[-1].strip().strip('"')


def shutdown_capability(action: str = "shutdown") -> tuple[bool, str]:
    """Czy proces MOZE wykonac akcje - pytanie do logind, nic nie wylacza.

    'challenge' znaczy "polkit zapyta o haslo". W nocy nikt go nie wpisze,
    wiec to jest odmowa, nie "prawie tak".
    """
    if not IS_LINUX:
        return False, t("priv.unsupported")
    spec = POWER_ACTIONS.get(action) or POWER_ACTIONS["shutdown"]
    if not spec.logind_can:
        return True, t("priv.not_needed")
    method = spec.logind_can
    answer = logind_can(method)
    if answer == "yes":
        return True, t("priv.logind_yes", method=method)
    if not answer:
        return False, t("priv.logind_unknown", method=method)
    if answer == "challenge":
        return False, t("priv.logind_challenge", method=method)
    return False, t("priv.logind_no", method=method, answer=answer)


def available_sleep_states() -> str:
    """Stany, na ktore logind faktycznie pozwala (CanSuspend/CanHibernate/...)."""
    if not IS_LINUX:
        return ""
    labels = (("CanSuspend", "suspend"), ("CanHibernate", "hibernate"),
              ("CanHybridSleep", "hybrid-sleep"))
    return ", ".join(label for method, label in labels if logind_can(method) == "yes")
