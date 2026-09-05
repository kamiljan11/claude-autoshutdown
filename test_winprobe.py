"""Testy warstwy systemowej: akcje zasilania i wykrywanie uprawnien.

Zaden test NIE wylacza ani nie usypia komputera - akcje sa albo sprawdzane
statycznie, albo z podmieniona funkcja uruchamiajaca procesy.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

import winprobe
from winprobe import POWER_ACTIONS, power_action, probe_process, shutdown_capability

WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="tylko Windows")


class FakeCompleted:
    def __init__(self, returncode: int, stderr: str = "", stdout: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


# --------------------------------------------------------------------------- #
# ksztalt komend
# --------------------------------------------------------------------------- #
def test_shutdown_wymusza_zamkniecie_aplikacji():
    """Bez /f Windows moze pokazac ekran blokujacy i czekac na klikniecie."""
    assert POWER_ACTIONS["shutdown"].args == ["shutdown", "/s", "/t", "0", "/f"]


def test_wariant_bez_wymuszania_nie_ma_f():
    assert "/f" not in winprobe._SHUTDOWN_POLITE


def test_zadna_komenda_nie_uzywa_c_bez_d():
    """`/c` bez `/d` to blad skladni - shutdown.exe wypisuje pomoc i konczy kodem 1."""
    for action in POWER_ACTIONS.values():
        if action.args and "/c" in action.args:
            assert "/d" in action.args, f"{action.key}: /c wymaga /d"


def test_uspienie_nie_czeka_na_zakonczenie():
    """rundll32 wraca dopiero po wybudzeniu - czekanie zawiesiloby program."""
    assert POWER_ACTIONS["sleep"].wait_for_exit is False
    assert POWER_ACTIONS["shutdown"].wait_for_exit is True


def test_akcje_niewymagajace_przywileju():
    assert POWER_ACTIONS["lock"].needs_privilege is False
    assert POWER_ACTIONS["nothing"].needs_privilege is False
    assert POWER_ACTIONS["shutdown"].needs_privilege is True


# --------------------------------------------------------------------------- #
# power_action(): sukces i - wazniejsze - porazka
# --------------------------------------------------------------------------- #
def test_akcja_nothing_nic_nie_uruchamia(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("akcja 'nothing' nie moze uruchamiac procesu")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    ok, detail = power_action("nothing")
    assert ok
    assert "nic nie wykonano" in detail


def test_nieznana_akcja_zwraca_blad():
    ok, detail = power_action("teleportacja")
    assert not ok
    assert "nieznana akcja" in detail


@WINDOWS_ONLY
def test_niezerowy_kod_wyjscia_to_porazka(monkeypatch):
    """Regresja: shutdown.exe potrafi odrzucic komende i wyjsc z kodem 1.

    Bez sprawdzania kodu program zameldowalby sukces, a komputer zostalby
    wlaczony na cala noc bez sladu dlaczego.
    """
    monkeypatch.setattr(subprocess, "run",
                        lambda *_a, **_k: FakeCompleted(1, stdout="Usage: shutdown.exe"))
    ok, detail = power_action("shutdown")
    assert not ok
    assert "NIEPOWODZENIE" in detail
    assert "kod 1" in detail
    assert "Usage" in detail


@WINDOWS_ONLY
def test_zerowy_kod_wyjscia_to_sukces(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: FakeCompleted(0))
    ok, detail = power_action("shutdown")
    assert ok
    assert "wykonane" in detail


@WINDOWS_ONLY
def test_force_false_uzywa_wariantu_bez_f(monkeypatch):
    seen: list[list[str]] = []

    def spy(args, **_k):
        seen.append(list(args))
        return FakeCompleted(0)

    monkeypatch.setattr(subprocess, "run", spy)
    power_action("shutdown", force=False)
    assert seen == [["shutdown", "/s", "/t", "0"]]


@WINDOWS_ONLY
def test_blad_uruchomienia_procesu_to_porazka(monkeypatch):
    def explode(*_a, **_k):
        raise OSError("nie znaleziono pliku")

    monkeypatch.setattr(subprocess, "run", explode)
    ok, detail = power_action("shutdown")
    assert not ok
    assert "nie udalo sie uruchomic" in detail


@WINDOWS_ONLY
def test_przekroczony_czas_nie_jest_traktowany_jak_porazka(monkeypatch):
    def slow(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="shutdown", timeout=25)

    monkeypatch.setattr(subprocess, "run", slow)
    ok, _ = power_action("shutdown")
    assert ok, "wolna komenda to nie to samo co odrzucona komenda"


# --------------------------------------------------------------------------- #
# uprawnienia i procesy - na zywej maszynie
# --------------------------------------------------------------------------- #
@WINDOWS_ONLY
def test_wylaczanie_dostepne_bez_admina():
    """Lokalne wylaczenie wymaga SeShutdownPrivilege, nie elewacji UAC."""
    can, why = shutdown_capability()
    assert can, f"brak przywileju wylaczania: {why}"
    assert "SeShutdownPrivilege" in why


@WINDOWS_ONLY
def test_probe_wlasnego_procesu():
    import os
    info = probe_process(os.getpid())
    assert info.alive
    assert info.created_filetime > 0
    assert "python" in info.name.lower()


def test_probe_nieistniejacego_procesu():
    assert not probe_process(999_999_999).alive
    assert not probe_process(-1).alive
