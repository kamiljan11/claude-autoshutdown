"""Cienka warstwa nad WinAPI (ctypes) - zero zaleznosci zewnetrznych.

Daje trzy rzeczy potrzebne monitorowi:
  * probe_process(pid)   -> czy PID zyje, jaki exe, kiedy wystartowal, ile zjadl CPU
  * human_idle_seconds() -> ile sekund uzytkownik nie ruszal mysza/klawiatura
  * power_action(name)   -> wykonanie akcji zasilania

Narzedzie jest Windows-only (ctypes.wintypes). Same wywolania sa oslonione
IS_WINDOWS, zeby import nie wysadzal testow logiki.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes
from dataclasses import dataclass

IS_WINDOWS = sys.platform == "win32"

# --- stale WinAPI -----------------------------------------------------------
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
FILETIME_PER_SECOND = 10_000_000  # FILETIME tyka co 100 ns

if IS_WINDOWS:
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32 = ctypes.WinDLL("user32", use_last_error=True)

    class _FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD),
                    ("dwHighDateTime", wintypes.DWORD)]

    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_FILETIME), ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME), ctypes.POINTER(_FILETIME)]
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.GetTickCount64.restype = ctypes.c_ulonglong
    _user32.GetLastInputInfo.argtypes = [ctypes.POINTER(_LASTINPUTINFO)]

    def _ft_to_int(ft: _FILETIME) -> int:
        return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


@dataclass(frozen=True)
class ProcInfo:
    """Migawka procesu. alive=False => reszta pol jest bez znaczenia."""
    pid: int
    alive: bool
    exe: str = ""
    created_filetime: int = 0
    cpu_100ns: int = 0  # kernel + user, narastajaco od startu procesu

    @property
    def name(self) -> str:
        return os.path.basename(self.exe)


DEAD = ProcInfo(pid=0, alive=False)


def probe_process(pid: int) -> ProcInfo:
    """Jedno otwarcie uchwytu -> nazwa exe, czas startu, zuzycie CPU.

    Uzywa PROCESS_QUERY_LIMITED_INFORMATION, wiec dziala bez podnoszenia
    uprawnien takze dla procesow innych sesji tego samego uzytkownika.
    """
    if not IS_WINDOWS or pid <= 0:
        return ProcInfo(pid=pid, alive=False)

    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ProcInfo(pid=pid, alive=False)
    try:
        creation, exited, kernel, user = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
        if not _kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exited),
                                         ctypes.byref(kernel), ctypes.byref(user)):
            return ProcInfo(pid=pid, alive=False)

        buf_len = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(buf_len.value)
        exe = buf.value if _kernel32.QueryFullProcessImageNameW(
            handle, 0, buf, ctypes.byref(buf_len)) else ""

        return ProcInfo(
            pid=pid,
            alive=True,
            exe=exe,
            created_filetime=_ft_to_int(creation),
            cpu_100ns=_ft_to_int(kernel) + _ft_to_int(user),
        )
    except OSError:
        # Proces zniknal miedzy OpenProcess a odczytem - traktujemy jak martwy.
        return ProcInfo(pid=pid, alive=False)
    finally:
        _kernel32.CloseHandle(handle)


def human_idle_seconds() -> float:
    """Ile sekund od ostatniego ruchu myszy / klawisza. -1 gdy nieznane."""
    if not IS_WINDOWS:
        return -1.0
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
    if not _user32.GetLastInputInfo(ctypes.byref(info)):
        return -1.0
    # GetTickCount64 nie przekreca sie po 49 dniach, dwTime (32-bit) juz tak.
    ticks = _kernel32.GetTickCount64()
    delta_ms = (ticks - info.dwTime) & 0xFFFFFFFF
    return delta_ms / 1000.0


# --- akcje zasilania --------------------------------------------------------
@dataclass(frozen=True)
class PowerAction:
    """Opis akcji zasilania.

    wait_for_exit=False dla uspienia: rundll32 nie wraca, dopoki komputer sie nie
    obudzi, wiec czekanie na jego zakonczenie zawiesiloby program.
    """
    key: str
    label: str
    args: list[str] | None
    wait_for_exit: bool = True
    needs_privilege: bool = True


# /f = zamknij aplikacje bez pytania. Bez tego Windows potrafi pokazac ekran
# "Ta aplikacja uniemozliwia zamkniecie" i CZEKAC na klikniecie - czyli dokladnie
# to, czego ma nie byc, gdy komputer ma zgasnac sam po nocy.
POWER_ACTIONS: dict[str, PowerAction] = {
    "shutdown": PowerAction("shutdown", "Wylacz komputer", ["shutdown", "/s", "/t", "0", "/f"]),
    "hibernate": PowerAction("hibernate", "Hibernacja", ["shutdown", "/h"]),
    "sleep": PowerAction("sleep", "Uspij (na maszynie z hibernacja zwykle hibernuje)",
                         ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                         wait_for_exit=False),
    "lock": PowerAction("lock", "Zablokuj ekran",
                        ["rundll32.exe", "user32.dll,LockWorkStation"],
                        needs_privilege=False),
    "nothing": PowerAction("nothing", "Nic nie rob (tylko log)", None,
                           needs_privilege=False),
}

# Wariant bez wymuszania - do wyboru przez uzytkownika, gdy woli ryzyko okienka
# blokujacego niz ryzyko utraty niezapisanej pracy.
_SHUTDOWN_POLITE = ["shutdown", "/s", "/t", "0"]

ACTION_TIMEOUT_SECONDS = 25


def power_action(name: str, force: bool = True) -> tuple[bool, str]:
    """Wykonuje akcje zasilania. Zwraca (czy sie powiodlo, opis dla logu).

    Kod wyjscia jest SPRAWDZANY: shutdown.exe potrafi odrzucic komende i wyjsc z
    kodem 1 (np. zla skladnia), a program bez tej kontroli zameldowalby sukces
    i zostawil komputer wlaczony na cala noc.
    """
    action = POWER_ACTIONS.get(name)
    if action is None:
        return False, f"nieznana akcja: {name}"
    if action.args is None:
        return True, f"{action.label} - nic nie wykonano"
    if not IS_WINDOWS:
        return False, f"{action.label} - pominieto (nie-Windows)"

    args = list(action.args)
    if name == "shutdown" and not force:
        args = list(_SHUTDOWN_POLITE)

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    printable = " ".join(args)
    if not action.wait_for_exit:
        subprocess.Popen(args, creationflags=flags)
        return True, f"{action.label} - komenda wyslana: {printable}"
    try:
        done = subprocess.run(args, capture_output=True, text=True, check=False,
                              timeout=ACTION_TIMEOUT_SECONDS, creationflags=flags)
    except subprocess.TimeoutExpired:
        return True, f"{action.label} - komenda dziala dluzej niz zwykle: {printable}"
    except OSError as exc:
        return False, f"{action.label} - nie udalo sie uruchomic: {exc}"

    if done.returncode == 0:
        return True, f"{action.label} - wykonane ({printable})"
    detail = (done.stderr or done.stdout or "").strip().splitlines()
    reason = detail[0] if detail else "bez komunikatu"
    return False, (f"{action.label} - NIEPOWODZENIE, kod {done.returncode}: "
                   f"{reason} | komenda: {printable}")


def cancel_pending_shutdown() -> None:
    """Anuluje zaplanowane `shutdown /s /t N` (gdy ktos ustawi opoznienie)."""
    if IS_WINDOWS:
        subprocess.run(["shutdown", "/a"], capture_output=True, check=False,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


# --- weryfikacja uprawnien (zero ryzyka - nic nie wylacza) -------------------
_SE_PRIVILEGE_ENABLED = 0x2
_TOKEN_ADJUST_PRIVILEGES = 0x20
_TOKEN_QUERY = 0x8


def shutdown_capability() -> tuple[bool, str]:
    """Czy proces MOZE wylaczyc komputer. Probuje wlaczyc SeShutdownPrivilege.

    To dokladnie ten przywilej, ktorego wymaga ExitWindowsEx / shutdown.exe.
    Wlaczenie przywileju w wlasnym tokenie NIE inicjuje zadnego zamkniecia,
    wiec sprawdzenie mozna robic przy kazdym starcie programu.
    """
    if not IS_WINDOWS:
        return False, "nie-Windows"

    class _LUID(ctypes.Structure):
        _fields_ = [("Low", wintypes.DWORD), ("High", wintypes.LONG)]

    class _LUID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Luid", _LUID), ("Attributes", wintypes.DWORD)]

    class _TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [("PrivilegeCount", wintypes.DWORD),
                    ("Privileges", _LUID_AND_ATTRIBUTES * 1)]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
            _kernel32.GetCurrentProcess(),
            _TOKEN_ADJUST_PRIVILEGES | _TOKEN_QUERY, ctypes.byref(token)):
        return False, "nie moge otworzyc tokenu procesu"
    try:
        luid = _LUID()
        if not advapi32.LookupPrivilegeValueW(None, "SeShutdownPrivilege",
                                              ctypes.byref(luid)):
            return False, "brak SeShutdownPrivilege w systemie"

        privileges = _TOKEN_PRIVILEGES(
            1, (_LUID_AND_ATTRIBUTES * 1)(
                _LUID_AND_ATTRIBUTES(luid, _SE_PRIVILEGE_ENABLED)))
        ctypes.set_last_error(0)
        ok = advapi32.AdjustTokenPrivileges(token, False, ctypes.byref(privileges),
                                            0, None, None)
        err = ctypes.get_last_error()
        if ok and err == 0:
            return True, "SeShutdownPrivilege wlaczony - wylaczanie dostepne"
        if err == 1300:  # ERROR_NOT_ALL_ASSIGNED
            return False, "konto nie ma przywileju wylaczania (ERROR_NOT_ALL_ASSIGNED)"
        return False, f"AdjustTokenPrivileges blad {err}"
    finally:
        _kernel32.CloseHandle(token)


def available_sleep_states() -> str:
    """Skrot z `powercfg /a` - ktore stany uspienia system faktycznie wspiera."""
    if not IS_WINDOWS:
        return ""
    try:
        out = subprocess.run(["powercfg", "/a"], capture_output=True, text=True,
                             timeout=10, check=False,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return ""
    supported: list[str] = []
    for line in out.stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.endswith(":"):
            continue
        if "not available" in stripped.lower() or "niedostep" in stripped.lower():
            break
        supported.append(stripped)
    return ", ".join(supported[:4])
