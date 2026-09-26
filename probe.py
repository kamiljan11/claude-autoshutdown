"""Wybor backendu systemowego. Reszta programu importuje TYLKO ten modul.

Oba backendy (winprobe, linuxprobe) wystawiaja ten sam kontrakt - patrz
docs/adr/0003-linux-backend.md. Silnik i UI nie wiedza, na jakim sa systemie.
"""

from __future__ import annotations

import sys

if sys.platform == "win32":
    import winprobe as backend
else:
    import linuxprobe as backend

PLATFORM = "windows" if sys.platform == "win32" else "linux"

FILETIME_PER_SECOND = backend.FILETIME_PER_SECOND          # jednostka cpu_100ns
PROC_START_UNITS_PER_SECOND = backend.PROC_START_UNITS_PER_SECOND  # jednostka procStart
POWER_ACTIONS = backend.POWER_ACTIONS
ProcInfo = backend.ProcInfo


def probe_process(pid: int) -> ProcInfo:
    return backend.probe_process(pid)


def human_idle_seconds() -> float:
    return backend.human_idle_seconds()


def power_action(name: str, force: bool = True) -> tuple[bool, str]:
    return backend.power_action(name, force=force)


def shutdown_capability(action: str = "shutdown") -> tuple[bool, str]:
    return backend.shutdown_capability(action)


def available_sleep_states() -> str:
    return backend.available_sleep_states()


def process_names() -> set[str] | None:
    """Nazwy procesow (male litery) albo None, gdy listy nie da sie odczytac."""
    return backend.process_names()


def claude_code_pids() -> list[int]:
    """PID-y sesji Claude Code wedlug systemu operacyjnego."""
    return backend.claude_code_pids()
